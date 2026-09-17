# Office

Word, Excel and PowerPoint in a Pantheon window, powered by ONLYOFFICE.

Open Office from the launcher, or open a supported file from Files:

| File | Editor | Edited output |
| --- | --- | --- |
| `.doc`, `.docx` | Document | `.docx` |
| `.xls`, `.xlsx` | Spreadsheet | `.xlsx` |
| `.ppt`, `.pptx` | Presentation | `.pptx` |

Use **File → Save to file** in the editor to write the current document back to the same Files/Fleet location. Legacy formats save beside the original with the modern extension. Existing sibling files and files changed outside Office are protected: download a copy if a conflict is reported. Files uploaded directly from the browser use **Download**.

Office retains a working copy on Hub while you edit. The footer indicates when that copy still needs to be written back to Files. Unwritten copies are retained in Recent working sessions, including after a desktop refresh. Closing a window with changes offers **Save & close**, **Keep working copy & close**, or **Keep editing**. If a save fails, the document remains available; do not treat a retained working copy as a file already written back to Files.

Files are limited to 512 MB. A connected Pantheon session and a configured document service are required. This shell-hosted App uses the installed desktop frontend; its editor is not bundled in individual forks. The Hub deployment guide `docs/office.md` describes service configuration.

## Agent interfaces

Office 0.2 exposes 18 window actions. Agents can inspect and modify live Word
paragraphs, Excel ranges/values/formulas, and PowerPoint text-box paragraphs,
as well as apply basic formatting. The editor updates in place; saving to Files
remains a separate explicit action. See **Skills & API** for parameters, limits
and examples. These actions require the matching native ONLYOFFICE content
bridge; ordinary open/edit/save still work without it.
