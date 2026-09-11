# Image Viewer

Inspect common image files with zoom, pan, rotation and a view at the image’s original pixel size.

## Using this App

Open an image from Files with **Image Viewer**. Use Fit to see the whole image, Actual size for 100% inspection, and zoom or drag to inspect details. Rotate changes the view by 90 degrees; it does not rewrite the source image. Reset returns to the default fitted view.

The window can download the image. Its file path keeps the source machine when opened from Files. Large multichannel microscopy datasets are better opened in an installed specialized viewer such as Viv.

## Window actions

Call these through `desktop_call` on this App’s existing window. The runtime’s action schema supplies parameters.

- `zoom_in`: Increase image magnification.
- `zoom_out`: Decrease image magnification.
- `fit`: Fit the image inside this window.
- `actual`: Display one image pixel per CSS pixel (100%).
- `rotate`: Rotate the view clockwise by 90 degrees. The original file is unchanged.
- `reset`: Reset rotation and fit the image to the window.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

Frontend entry: [frontend/main.js](frontend/main.js).
