// Create a function which handles the drop of file(s) on top of the div dropZoneId.
// Files will be passed to inputId. File type validation happens at submit time.
function setDropHandler(dropZoneId, inputId){
    console.log("setting drop handler and firing change event on input")
    const dropZone = document.getElementById(dropZoneId);
    const inputDiv = document.getElementById(inputId);

    function dropHandler(ev) {
        console.log("File(s) dropped");
        dropZone.classList.remove("drag_drop_zone"); // Remove highlight when file leaves
        // Prevent default behavior (Prevent file from being opened)
        ev.preventDefault();

        let fileToSet = null;

        if (ev.dataTransfer.items) {
            if (ev.dataTransfer.items.length !== 1){
                console.log("Multiple files are not supported yet")
                return
            }
            const item = ev.dataTransfer.items[0];
            if (item.kind === "file") {
                const file = item.getAsFile();
                if (!file) {
                    // getAsFile() returns null for directories in some browsers;
                    // submit-time validation will show the appropriate error.
                    return;
                }
                fileToSet = file;
                console.log(`… file.name = ${file.name}`);
            }
        } else {
            if (ev.dataTransfer.files.length !== 1){
                console.log("Multiple files are not supported yet")
                return
            }
            fileToSet = ev.dataTransfer.files[0];
            console.log(`… file.name = ${fileToSet.name}`);
        }

        if (fileToSet) {
            const dataTransfer = new DataTransfer();
            dataTransfer.items.add(fileToSet);
            inputDiv.files = dataTransfer.files;
            const changeEvent = new Event('change');
            inputDiv.dispatchEvent(changeEvent);
        }
    }

    function dragLeaveHandler(ev) {
        dropZone.classList.remove("drag_drop_zone"); // Remove highlight when file leaves
    }


    // Prevent default behaviour of drag
    function dragOverHandler(ev) {
        console.log("File(s) in drop zone");

        dropZone.classList.add("drag_drop_zone"); // Remove highlight when file leaves
        // Prevent default behavior (Prevent file from being opened)
        ev.preventDefault();
    }


    console.assert(dropZone != null)
    dropZone.ondrop = dropHandler;
    dropZone.ondragover = dragOverHandler;
    dropZone.ondragleave = dragLeaveHandler;
}
