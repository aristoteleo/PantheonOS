# ImageJ.js

An independently versioned Atrium App. The stable App id remains `imagej`.
The package contains its icon, complete frontend, ImJoy controls and a private
image preparation backend. Desktop supplies only the standard App SDK.
The included SDK host runs on the workspace data-server origin, separate from
the Desktop. This preserves ImageJ.js's IndexedDB, local storage and nested
runtime without giving a user-installed App access to the Desktop document.

Build the frontend with `npm ci && npm run build` in this directory. The
committed `frontend/index.js` is ready to install without a build step.

In Store, create a user copy to obtain an independent Git repository, customize
it and publish an immutable version. The first browser launch downloads the
ImageJ.js/ImJoy runtime; it requires access to ij.imjoy.io and lib.imjoy.io.
