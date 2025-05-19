let dialog = null;
let confirmButton = null;
let cancelButton = null;

document.addEventListener('DOMContentLoaded', (event) => {
    dialog = document.getElementById('actionCancelDialog');
    if (dialog) {
        confirmButton = dialog.querySelector('.dialog-action');
        cancelButton = dialog.querySelector('.dialog-cancel');
        if (cancelButton) {
            cancelButton.addEventListener('click', () => {
                ui("#actionCancelDialog");
            });
        }
    }
});

function killDialog(title, message, confirmText, onConfirm) {
    dialog.querySelector('.dialog-title').textContent = title;
    dialog.querySelector('.dialog-message').textContent = message;
    confirmButton.textContent = confirmText;
    const newConfirmButton = confirmButton.cloneNode(true);
    confirmButton.parentNode.replaceChild(newConfirmButton, confirmButton);
    confirmButton = newConfirmButton;
    confirmButton.addEventListener('click', async () => {
        ui("#actionCancelDialog");
        if (onConfirm) {
            await onConfirm();
        }
    });
    ui("#actionCancelDialog");
}
