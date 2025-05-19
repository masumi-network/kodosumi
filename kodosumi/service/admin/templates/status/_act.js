document.addEventListener('DOMContentLoaded', (event) => {
    trashButton = document.getElementById('trash-button');
    elmStatusIcon = document.getElementById('status-icon');
    if (trashButton) {
        trashButton.addEventListener('click', () => {
            if (elmStatusIcon.textContent === "pause") {
                killDialog(
                    "Kill Execution", 
                    "Are you sure you want to kill and delete this agentic execution?", 
                    "Yes",
                    async () => { await doKill() }
                );
            } else {
                killDialog(
                    "Delete Execution", 
                    "Are you sure you want to delete this agentic execution?", 
                    "Yes",
                    async () => { await doKill() }
                );
            }
        });
    }
});

async function doKill() {
    const response = await fetch(`/outputs/${fid}`, {
        method: 'DELETE'
    });
    if (response.ok) {
        window.location.href = '/timeline/view';
    }
}    
