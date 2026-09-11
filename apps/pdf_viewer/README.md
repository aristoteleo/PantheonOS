# PDF Viewer

Read PDFs in a desktop window with page navigation, zoom and page-text inspection.

## Using this App

Open a `.pdf` file from Files. Navigate pages and adjust zoom inside the window. Agents can use `setPage` with a one-based page number, `setZoom` with a scale from 0.25 to 4, or `getText` for text on a selected page.

The viewer uses bundled PDF.js assets. It reads the PDF through the Desktop file bridge and does not edit the original document. Image-only/scanned pages may have no extractable text; displaying a page is distinct from performing OCR.

## Window actions

Call these through `desktop_call` on this App’s existing window. The runtime’s action schema supplies parameters.

- `setPage`: Render a page in this PDF window (one-based).
- `setZoom`: Render this PDF at a scale from 0.25 to 4.
- `getText`: Read text from a page of the currently opened PDF; defaults to the visible page.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

Frontend entry: [frontend/main.js](frontend/main.js).
