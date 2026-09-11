# ImageGeneration

Generate image files from text prompts using the workspace’s configured image-model provider.

## Using this App

Call `generate_image` with a description and the output options exposed by the tool schema. Inspect the returned file before using it in a report or opening it in Image Viewer. Register a finished deliverable in the task’s outputs when it should be visible to the user.

Generation requires a configured image provider and its credentials or account access. The exact models and parameters depend on that configuration. This backend creates images; Image Viewer displays existing images.

## Agent interface

Available tools: `generate_image`. See [app.json](app.json) for the declared tool contract; runtime discovery provides the current parameter schema.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

Backend implementation: [__init__.py](__init__.py).
