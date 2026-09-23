# Keep the upstream engine and Python dependency lock unchanged. Ubuntu's home
# is 0750 in this image, which breaks Python's absolute stdlib paths when Fleet
# uses a different node-owner UID. Only make the image's home traversable;
# private host state and weights keep their owner-only, read-only mounts.
FROM ghcr.io/speaches-ai/speaches@sha256:2163775b6df5e451a71200e8f675fed68dbd8ab184fc604453d549e486f22fd2
USER root
RUN chmod 0755 /home/ubuntu
# Catch access regressions without granting the image's ubuntu group to callers.
USER 65532:65532
RUN /home/ubuntu/speaches/.venv/bin/python -c "import encodings, speaches, uvicorn, onnxruntime, faster_whisper"
USER 1000:1000
