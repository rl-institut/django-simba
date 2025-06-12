// Create a function which handles the drop of file(s) on top of the div dropZoneId.
// Files will be passed to inputId
function setDropHandler(dropZoneId, inputId){
    console.log("setting drop handler and firing change event on input")
    const dropZone = document.getElementById(dropZoneId);
    const inputDiv = document.getElementById(inputId);

    function dropHandler(ev) {
        console.log("File(s) dropped");
        dropZone.classList.remove("drag_drop_zone"); // Remove highlight when file leaves
        // Prevent default behavior (Prevent file from being opened)
        ev.preventDefault();

        if (ev.dataTransfer.items) {
            if (ev.dataTransfer.items.length !== 1){
                console.log("Multiple files are not supported yet")
                return
            }
            // Use DataTransferItemList interface to access the file(s)
            [...ev.dataTransfer.items].forEach((item, i) => {
                // If dropped items aren't files, reject them
                if (item.kind === "file") {
                    const file = item.getAsFile();
                    console.log(`… file[${i}].name = ${file.name}`);

                    // Set the file to the input field
                    const dataTransfer = new DataTransfer(); // Create a new DataTransfer object
                    dataTransfer.items.add(file); // Add the file to DataTransfer
                    inputDiv.files = dataTransfer.files;

                }

            });
        } else {
            if (ev.dataTransfer.files.length !== 1){
                console.log("Multiple files are not supported yet")
                return
            }
            // Use DataTransfer interface to access the file(s)
            [...ev.dataTransfer.files].forEach((file, i) => {
                console.log(`… file[${i}].name = ${file.name}`);

                // Set the file to the input field
                const dataTransfer = new DataTransfer(); // Create a new DataTransfer object
                dataTransfer.items.add(file); // Add the file to DataTransfer
                inputDiv.files = dataTransfer.files; // Assign files to the input field
            });
        };
        const changeEvent = new Event('change', );
        inputDiv.dispatchEvent(changeEvent);

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
