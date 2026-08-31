// BatchMiniMax — folder-picker button for the BatchMiniMaxLoader node.
// Adds a native "Select Folder" dialog (webkitdirectory) and writes the
// chosen path into the node's `folder_path` widget.

import { app } from "../../scripts/app.js";

const NODE_NAME = "BatchMiniMaxLoader";
const WIDGET_NAME = "folder_path";

app.registerExtension({
    name: "BatchMiniMax.SelectFolder",
    async nodeCreated(node) {
        if (node.comfyClass !== NODE_NAME && node.constructor?.type !== NODE_NAME) {
            return;
        }

        const folderWidget = node.widgets?.find((w) => w.name === WIDGET_NAME);
        if (!folderWidget) return;

        // Button widget that opens a native folder picker.
        node.addWidget("button", "Select Folder", "open", () => {
            const input = document.createElement("input");
            input.type = "file";
            input.setAttribute("webkitdirectory", "");
            input.setAttribute("directory", "");
            input.style.display = "none";
            document.body.appendChild(input);

            input.addEventListener("change", () => {
                const files = input.files;
                if (!files || files.length === 0) {
                    input.remove();
                    return;
                }
                // Prefer the browser's full path (Chromium), else derive the
                // parent directory from webkitRelativePath.
                let path = files[0].path;
                if (!path && files[0].webkitRelativePath) {
                    const rel = files[0].webkitRelativePath; // e.g. "myfolder/clip_001.mp4"
                    const idx = rel.indexOf("/");
                    path = idx >= 0 ? rel.slice(0, idx) : rel;
                }
                if (!path) return;

                // Preserve any leading prefix the user may have left in the
                // filename-relative workflow (e.g. "input/" style). If the raw
                // path already starts with "/" or a drive letter, use as-is.
                folderWidget.value = path;

                // Refresh the widget value display.
                if (typeof folderWidget.callback === "function") {
                    folderWidget.callback(path);
                }
                node.setDirtyCanvas(true, true);
                input.remove();
            });

            input.click();
        });

        // Keep the button un-serialized so it is not saved into prompts.
        const btn = node.widgets?.[node.widgets.length - 1];
        if (btn) {
            btn.serializeValue = async () => undefined;
        }
    },
});
