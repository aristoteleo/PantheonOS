/* Pantheon desktop bridge, loaded by QuPath's official qupath.startup.script.
 * Runs inside the displayed QuPath GUI JVM. No extension or headless instance.
 * Reference: QuPath v0.7.0 QuPathGUI.maybeRunStartupScript and runScript.
 */
import com.google.gson.Gson
import com.google.gson.GsonBuilder
import javafx.application.Platform
import qupath.lib.gui.QuPathGUI
import qupath.lib.gui.scripting.QPEx
import qupath.lib.gui.scripting.languages.GroovyLanguage
import qupath.lib.scripting.ScriptParameters
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.nio.file.attribute.PosixFilePermissions
import java.security.MessageDigest
import java.util.concurrent.Callable
import java.util.concurrent.FutureTask

class PantheonRequestExpired extends RuntimeException {
    PantheonRequestExpired() { super('Request expired before script execution') }
}

class PantheonBoundedWriter extends Writer {
    final StringBuilder buffer = new StringBuilder()
    final int limit = 65536
    boolean truncated = false
    synchronized void write(char[] chars, int offset, int length) {
        int count = Math.min(length, limit - buffer.length())
        if (count > 0) buffer.append(chars, offset, count)
        if (count < length) truncated = true
    }
    void flush() {}
    void close() {}
    synchronized String toString() { buffer.toString() }
}

class PantheonQuPathBridge {
    final QuPathGUI gui
    final Path directory
    final String session
    final String token
    final Gson gson = new GsonBuilder().serializeNulls().create()
    final Set<String> seen = new HashSet<>()
    // Accessed only on JavaFX. Tokens distinguish a reloaded file's new
    // in-memory data from its older, potentially unsaved ImageData instance.
    final Map<Object, String> imageTokens = new WeakHashMap<>()

    PantheonQuPathBridge(QuPathGUI gui, Path directory, String session, String token) {
        this.gui = gui; this.directory = directory; this.session = session; this.token = token
    }

    static Object fx(Closure work) {
        if (Platform.isFxApplicationThread()) return work.call()
        def task = new FutureTask(work as Callable)
        Platform.runLater(task)
        // This wait occurs only on our daemon worker. Python observes the
        // request's running state without waiting for a blocked JavaFX thread.
        try { return task.get() }
        catch (java.util.concurrent.ExecutionException error) { throw (error.cause ?: error) }
    }

    void write(String name, Map value) {
        String json = gson.toJson(value)
        if (json.getBytes('UTF-8').length > 1_000_000)
            throw new IllegalArgumentException('Bridge response exceeds 1 MB; return a smaller summary')
        Path temporary = Files.createTempFile(directory, '.reply-', '.tmp',
                PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString('rw-------')))
        try {
            Files.writeString(temporary, json)
            Files.move(temporary, directory.resolve(name), StandardCopyOption.ATOMIC_MOVE,
                    StandardCopyOption.REPLACE_EXISTING)
        } finally { Files.deleteIfExists(temporary) }
    }

    static Object jsonValue(Object value, int depth = 0, int[] count = [0] as int[]) {
        if (++count[0] > 10000 || depth > 12)
            throw new IllegalArgumentException('Script result is too large or deeply nested')
        if (value == null || value instanceof Boolean) return value
        if (value instanceof CharSequence) {
            if (value.length() > 262144) throw new IllegalArgumentException('Script result string exceeds 256 KB')
            return value.toString()
        }
        if (value instanceof Number) {
            if (!Double.isFinite(value.doubleValue()))
                throw new IllegalArgumentException('Script result contains a non-finite number')
            return value
        }
        if (value instanceof Map) {
            Map result = [:]
            value.each { k, v ->
                if (!(k instanceof CharSequence)) throw new IllegalArgumentException('Result map keys must be strings')
                result[k.toString()] = jsonValue(v, depth + 1, count)
            }
            return result
        }
        if (value instanceof Collection)
            return value.collect { jsonValue(it, depth + 1, count) }
        throw new IllegalArgumentException('Return JSON values, not QuPath or Java objects: ' + value.getClass().name)
    }

    String imageToken(Object image) {
        if (image == null) return null
        String token = imageTokens.get(image)
        if (token == null) {
            token = UUID.randomUUID().toString()
            imageTokens.put(image, token)
        }
        return token
    }

    static void checkExpiry(double expiresAt) {
        if (expiresAt < System.currentTimeMillis() / 1000.0)
            throw new PantheonRequestExpired()
    }

    static Object finite(Number value) {
        return value != null && Double.isFinite(value.doubleValue()) ? value : null
    }

    Map captureContext(Map params, double expiresAt) {
        // Called on FX immediately before execution. A queued operation must
        // not silently follow the user to a different image in this window.
        checkExpiry(expiresAt)
        def image = gui.getImageData()
        if (params.containsKey('expected_image')) {
            def expected = params.expected_image
            if (expected != null && (!(expected instanceof String) ||
                    !(expected ==~ /[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}/)))
                throw new IllegalArgumentException('expected_image must be an image_token from state, or null')
            if (expected != imageToken(image))
                throw new IllegalStateException('The current image changed; read its state before submitting a new script')
        }
        return [image: image, project: gui.getProject()]
    }

    Map state(Map params) {
        int limit = (params.annotation_limit == null ? 200 : params.annotation_limit) as int
        if (limit < 0 || limit > 2000) throw new IllegalArgumentException('annotation_limit must be 0–2000')
        def image = gui.getImageData()
        def project = gui.getProject()
        def viewer = gui.getViewer()
        def hierarchy = image?.getHierarchy()
        def annotations = hierarchy?.getAnnotationObjects() ?: []
        def selection = hierarchy?.getSelectionModel()?.getSelectedObjects() ?: []
        def server = image?.getServer()
        def entry = image != null && project != null ? project.getEntry(image) : null
        return [
            protocol_version: 1, qupath_version: QuPathGUI.getVersion().toString(),
            image_token: imageToken(image),
            capabilities: ['state', 'script', 'script_status'],
            image: image == null ? null : [
                name: server.getMetadata().getName(), path: server.getPath(),
                uris: server.getURIs().collect { it.toString() },
                width: server.getWidth(), height: server.getHeight(),
                channels: server.nChannels(), z_slices: server.nZSlices(), timepoints: server.nTimepoints(),
                image_type: image.getImageType().toString(), changed: image.isChanged(),
                project_entry_id: entry?.getID(), project_entry_name: entry?.getImageName()
            ],
            project: project == null ? null : [uri: project.getURI()?.toString(),
                name: project.getName(), image_count: project.getImageList().size()],
            viewer: viewer == null ? null : [center_x: finite(viewer.getCenterPixelX()),
                center_y: finite(viewer.getCenterPixelY()), downsample: finite(viewer.getDownsampleFactor()),
                rotation: finite(viewer.getRotation()), z: viewer.getZPosition(), t: viewer.getTPosition()],
            objects: [annotation_count: annotations.size(), detection_count: hierarchy?.getDetectionObjects()?.size() ?: 0,
                selected_count: selection.size(),
                selected_ids: selection.take(2000).collect { it.getID().toString() },
                selected_truncated: selection.size() > 2000,
                annotations_truncated: annotations.size() > limit,
                annotations: annotations.take(limit).collect { obj ->
                    def roi = obj.getROI()
                    [id: obj.getID().toString(), name: obj.getName(), classification: obj.getPathClass()?.toString(),
                     locked: obj.isLocked(), roi: roi == null ? null : [type: roi.getRoiName(),
                        x: roi.getBoundsX(), y: roi.getBoundsY(), width: roi.getBoundsWidth(),
                        height: roi.getBoundsHeight(), z: roi.getZ(), t: roi.getT()]]
                }]
        ]
    }

    Object script(Map params, PantheonBoundedWriter stdout, PantheonBoundedWriter stderr, double expiresAt) {
        if (!(params.script instanceof String) || params.script.isBlank())
            throw new IllegalArgumentException('script must be nonempty Groovy source')
        String thread = params.thread ?: 'worker'
        if (!(thread in ['worker', 'fx'])) throw new IllegalArgumentException('thread must be worker or fx')
        if (params.containsKey('update_hierarchy') && !(params.update_hierarchy instanceof Boolean))
            throw new IllegalArgumentException('update_hierarchy must be a boolean')
        boolean updateHierarchy = !params.containsKey('update_hierarchy') || params.update_hierarchy
        def run = { context ->
            checkExpiry(expiresAt)
            def arguments = (params.args ?: []) as String[]
            def scriptParams = ScriptParameters.builder()
                    .setProject(context.project).setImageData(context.image)
                    .setDefaultImports(QPEx.getCoreClasses())
                    .setDefaultStaticImports(Collections.singletonList(QPEx.class))
                    .setScript(params.script).setArgs(arguments)
                    .setWriter(stdout).setErrorWriter(stderr)
                    .setBatchSaveResult(false).doUpdateHierarchy(false).build()
            Object result
            try {
                result = GroovyLanguage.getInstance().execute(scriptParams)
            } finally {
                // Hierarchy notifications reach JavaFX from its own thread even
                // when a long analysis script mutates objects on the worker.
                // Read-only/export/save scripts can skip this notification,
                // which otherwise marks even a freshly saved image as changed.
                // Skipping it never clears changes already present in the image.
                if (updateHierarchy && context.image != null)
                    Platform.runLater { context.image.getHierarchy().fireHierarchyChangedEvent(this) }
            }
            return jsonValue(result)
        }
        if (thread == 'fx') {
            // One FX callback: no user event can switch viewers between the
            // expected-image check and a short view-changing script.
            return fx { run.call(captureContext(params, expiresAt)) }
        }
        def context = fx { captureContext(params, expiresAt) }
        return run.call(context)
    }

    void process(Path path) {
        String id = path.fileName.toString().replaceFirst(/\.request\.json$/, '')
        if (!(id ==~ /[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}/) || seen.contains(id)) return
        Map response = [session_id: session, request_id: id]
        // Preserve terminal and running records if this script is accidentally
        // installed twice in a JVM; never replay an indeterminate mutation.
        if (Files.exists(directory.resolve(id + '.response.json'))) { seen.add(id); return }
        try {
            if (Files.size(path) > 600000) throw new IllegalArgumentException('Request exceeds 600 KB')
            Map request = gson.fromJson(Files.readString(path), Map.class)
            if (request.session_id != session || request.request_id != id ||
                !(request.token instanceof String) || !MessageDigest.isEqual(
                    token.getBytes('UTF-8'), request.token.getBytes('UTF-8')))
                throw new SecurityException('Invalid bridge session credentials')
            seen.add(id)
            if (!(request.method in ['state', 'script'])) throw new IllegalArgumentException('Unknown bridge method')
            if (!(request.params instanceof Map) || !(request.expires_at instanceof Number))
                throw new IllegalArgumentException('Invalid bridge request')
            if (request.expires_at.doubleValue() < System.currentTimeMillis() / 1000.0) {
                write(id + '.response.json', response + [state: 'expired', error: 'Request expired before execution'])
                return
            }
            response.started_at = System.currentTimeMillis() / 1000.0
            write(id + '.response.json', response + [state: 'running'])
            def stdout = new PantheonBoundedWriter()
            def stderr = new PantheonBoundedWriter()
            try {
                def result = request.method == 'state' ? fx { state(request.params) } :
                        script(request.params, stdout, stderr, request.expires_at.doubleValue())
                write(id + '.response.json', response + [state: 'succeeded', result: result,
                    stdout: stdout.toString(), stderr: stderr.toString(),
                    output_truncated: stdout.truncated || stderr.truncated,
                    finished_at: System.currentTimeMillis() / 1000.0])
            } catch (Throwable error) {
                String detail = error.getMessage() ?: error.getClass().name
                write(id + '.response.json', response + [state: error instanceof PantheonRequestExpired ? 'expired' : 'failed', error: detail.take(16000),
                    stdout: stdout.toString(), stderr: stderr.toString(),
                    output_truncated: stdout.truncated || stderr.truncated,
                    finished_at: System.currentTimeMillis() / 1000.0])
            }
        } catch (Exception error) {
            seen.add(id)
            write(id + '.response.json', response + [state: 'failed', error: (error.getMessage() ?: 'Invalid request').take(16000)])
        }
    }

    void start() {
        write('ready.json', [session_id: session, protocol_version: 1, pid: ProcessHandle.current().pid(),
            qupath_version: QuPathGUI.getVersion().toString(), capabilities: ['state', 'script', 'script_status']])
        def worker = new Thread({
            while (!Thread.currentThread().isInterrupted()) {
                try {
                    Files.newDirectoryStream(directory, '*.request.json').withCloseable { paths ->
                        paths.toList().findAll {
                            !seen.contains(it.fileName.toString().replaceFirst(/\.request\.json$/, ''))
                        }.sort { a, b ->
                            Files.getLastModifiedTime(a).compareTo(Files.getLastModifiedTime(b))
                        }.each { process(it) }
                    }
                    Thread.sleep(50)
                } catch (InterruptedException ignored) { break }
                catch (Exception error) { System.err.println('Pantheon QuPath bridge: ' + error.getClass().simpleName) }
            }
        }, 'pantheon-qupath-bridge')
        worker.setDaemon(true)
        worker.start()
    }
}

String bridgePath = System.getenv('PANTHEON_QUPATH_BRIDGE_DIR')
String bridgeSession = System.getenv('PANTHEON_QUPATH_BRIDGE_SESSION')
String bridgeToken = System.getenv('PANTHEON_QUPATH_BRIDGE_TOKEN')
if (bridgePath && bridgeSession && bridgeToken) {
    Path path = Path.of(bridgePath)
    if (!Files.isDirectory(path) || Files.isSymbolicLink(path) ||
        Files.getPosixFilePermissions(path) != PosixFilePermissions.fromString('rwx------'))
        throw new SecurityException('QuPath bridge directory must be a private 0700 directory')
    // The same JVM cannot install a second worker that races the first.
    synchronized (System.getProperties()) {
        if (System.getProperty('pantheon.qupath.bridge.started') == null) {
            System.setProperty('pantheon.qupath.bridge.started', bridgeSession)
            new PantheonQuPathBridge(QuPathGUI.getInstance(), path, bridgeSession, bridgeToken).start()
        }
    }
}
return null
