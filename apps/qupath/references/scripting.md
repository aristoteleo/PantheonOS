# Live QuPath scripting examples

Submit each Groovy block through `desktop_call(window_id,
action="run_script", args={"script": source, "thread": "worker", "args": [...],
"expected_image": image_token, "request_id": unique_id})`.
Take `image_token` from a completed `desktop_read(window_id)` result's inner
GUI state. Keep that token while preparing an operation; do not refresh it
blindly after a mismatch. ROI changes and exports below require this guard.
Use the `request_id` to retrieve a pending result. These examples operate on
the selected window's live image, not a newly loaded copy from disk.
Pass `update_hierarchy=False` for the inspection and export examples below;
leave it `True` (the default) for the rectangle annotation mutation. Disabling
the automatic notification preserves any unsaved changes already present.

The methods below were checked against the QuPath 0.7.0 source. Official
references: [scripting](https://qupath.readthedocs.io/en/stable/docs/scripting/overview.html),
[QP 0.7.0](https://github.com/qupath/qupath/blob/v0.7.0/qupath-core-processing/src/main/java/qupath/lib/scripting/QP.java),
[QPEx 0.7.0](https://github.com/qupath/qupath/blob/v0.7.0/qupath-gui-fx/src/main/java/qupath/lib/gui/scripting/QPEx.java).
For new plugin-specific calls, check the installed version or the script
produced by QuPath's workflow history before choosing parameters.

## Inspect annotations and calibration

This returns at most 100 annotations, with stable object IDs and full-image
pixel coordinates. Micron calibration may be absent; do not turn missing
calibration into an assumed physical scale.

```groovy
import static qupath.lib.gui.scripting.QPEx.*

def image = getCurrentImageData()
if (image == null) throw new IllegalStateException('No image is open')
def server = image.getServer()
def calibration = server.getPixelCalibration()
def finiteOrNull = { double value -> Double.isFinite(value) ? value : null }
def annotations = getAnnotationObjects()
return [
    image: server.getMetadata().getName(),
    width: server.getWidth(), height: server.getHeight(),
    pixelWidthMicrons: finiteOrNull(calibration.getPixelWidthMicrons()),
    pixelHeightMicrons: finiteOrNull(calibration.getPixelHeightMicrons()),
    changed: image.isChanged(),
    count: annotations.size(),
    selectedIds: getSelectedObjects().collect { it.getID().toString() },
    annotations: annotations.take(100).collect { object ->
        def roi = object.getROI()
        [id: object.getID().toString(), name: object.getName(),
         classification: object.getPathClass()?.toString(),
         roi: roi == null ? null : [x: roi.getBoundsX(), y: roi.getBoundsY(),
             width: roi.getBoundsWidth(), height: roi.getBoundsHeight(),
             z: roi.getZ(), t: roi.getT(), areaPixels: roi.getArea()]]
    }
]
```

## Add one requested rectangular annotation

Pass `args=[x, y, width, height, z, t, name]`, all strings. These are coordinates
in the **full-resolution image**, not screenshot coordinates. Use the image
and plane confirmed by `desktop_read`. This mutates the current hierarchy;
submit it once, then check the returned ID and take a screenshot.

```groovy
import static qupath.lib.gui.scripting.QPEx.*
import qupath.lib.regions.ImagePlane
import qupath.lib.roi.ROIs
import qupath.lib.objects.PathObjects

def image = getCurrentImageData()
if (image == null) throw new IllegalStateException('No image is open')
def server = image.getServer()
double x = args[0].toDouble(), y = args[1].toDouble()
double width = args[2].toDouble(), height = args[3].toDouble()
int z = args[4].toInteger(), t = args[5].toInteger()
if (![x, y, width, height].every { Double.isFinite(it) } ||
    x < 0 || y < 0 || width <= 0 || height <= 0 ||
    x + width > server.getWidth() || y + height > server.getHeight() ||
    z < 0 || z >= server.nZSlices() || t < 0 || t >= server.nTimepoints())
    throw new IllegalArgumentException('Rectangle or plane is outside the image')
def roi = ROIs.createRectangleROI(x, y, width, height, ImagePlane.getPlane(z, t))
def annotation = PathObjects.createAnnotationObject(roi)
annotation.setName(args[6])
addObject(annotation)
return [id: annotation.getID().toString(), annotationCount: getAnnotationObjects().size()]
```

## Export current annotations and their measurements

Pass two absolute workspace output paths as `args=[geoJsonPath, tablePath]`.
The example expects new output files to avoid confusing a stale export with
a successful write. Choose a new path unless the user intends replacement.
QuPath's measurement table delimiter follows its preferences; inspect the
header before reading it as CSV or TSV. The exporter logs some I/O failures
instead of throwing them, so verify the files after the script completes.

```groovy
import static qupath.lib.gui.scripting.QPEx.*

def image = getCurrentImageData()
if (image == null) throw new IllegalStateException('No image is open')
def targets = args.take(2).collect { new File(it) }
if (targets.size() != 2 || targets.any { !it.isAbsolute() || it.exists() })
    throw new IllegalArgumentException('Provide two new absolute output paths')
targets.each { file ->
    if (!file.getParentFile().isDirectory() && !file.getParentFile().mkdirs())
        throw new IOException('Cannot create output directory')
}
exportObjectsToGeoJson(getAnnotationObjects(), targets[0].getPath(), 'FEATURE_COLLECTION')
saveAnnotationMeasurements(image, targets[1].getPath())
if (targets.any { !it.isFile() || it.length() == 0 })
    throw new IOException('Export did not create both nonempty files')
return [annotations: getAnnotationObjects().size(),
        files: targets.collect { [path: it.getPath(), bytes: it.length()] }]
```

GeoJSON preserves object properties as well as shape coordinates; exports use
full-resolution image pixels with a top-left origin. A rendered screenshot
has a different coordinate system. See the official
[annotation export guide](https://qupath.readthedocs.io/en/stable/docs/advanced/exporting_annotations.html).

## Save the current image data

Pass a new absolute workspace `.qpdata` path as `args=[outputPath]`, the checked
`expected_image`, and `update_hierarchy=False`. This saves annotations and
other image data, not a copy of the source slide. Check the file and then
`desktop_read`'s `image.changed` before requesting a normal close. For a
project workflow, use the project's save operation instead of assuming that
a standalone `.qpdata` export also updates the project entry.

```groovy
def image = getCurrentImageData()
if (image == null) throw new IllegalStateException('No image is open')
def target = new File(args[0])
if (!target.isAbsolute() || target.exists() || !target.getParentFile().isDirectory())
    throw new IllegalArgumentException('Provide a new absolute path in an existing directory')
qupath.lib.io.PathIO.writeImageData(target, image)
if (!target.isFile() || target.length() == 0)
    throw new IOException('Image data was not saved')
return [path: target.getPath(), bytes: target.length(), changed: image.isChanged()]
```

## Enter text in a verified focused field

JavaFX's native X11 key path cannot represent non-BMP characters reliably.
For such text, first focus the intended input through its native child
`window_id` and inspect its screenshot. Run this on `thread="fx"` with
`update_hierarchy=False`, the checked `expected_image`, and
`args=[expectedWindowTitle, text]`. Supply the exact current title from
`desktop_read`; a title/focus mismatch fails without typing. This changes the
existing field's selection, without executing or submitting it. Confirm the
returned text or screenshot before the next action.

```groovy
def windows = javafx.stage.Window.getWindows().findAll { it.isFocused() }
if (windows.size() != 1 || !(windows[0] instanceof javafx.stage.Stage) ||
    windows[0].getTitle() != args[0])
    throw new IllegalStateException('The expected window is not focused')
def field = windows[0].getScene().getFocusOwner()
if (!(field instanceof javafx.scene.control.TextInputControl) ||
    !field.isEditable() || field.isDisabled())
    throw new IllegalStateException('The focused control is not an editable text field')
field.replaceSelection(args[1])
return [window: windows[0].getTitle(), fieldId: field.getId(), text: field.getText()]
```
