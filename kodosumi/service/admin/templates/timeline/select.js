document.addEventListener('DOMContentLoaded', (event) => {
    // Initialisierung der DOM-Elemente
    selectAll = document.getElementById('select-all');
    selectAllLabel = document.getElementById('select-all-label');
    deleteSelect = document.getElementById('delete-select');
    if (selectAll) {
        selectAll.addEventListener('click', handleSelect);
    }
    if (deleteSelect) {
        deleteSelect.addEventListener('click', handleDelete);
    }
});

function listItems() {
    if (!container) return [];
    return container.getElementsByTagName('li');
}

function count() {
    if (!container) return 0;
    return listItems().length;
}

function allChecked() {
    if (!container) return false;
    const items = listItems();
    if (items.length === 0) return false;
    return Array.from(items).every(item => {
        const checkbox = item.querySelector('input[type="checkbox"]');
        return checkbox && checkbox.checked;
    });
}

function updateAction() {
    if (allChecked()) {
        selectAll.innerText = "indeterminate_check_box";
        if (!hasReachedEnd) {
            const loadMoreLink = document.createElement('a');
            loadMoreLink.className = 'underline error-text';
            loadMoreLink.href = '#';
            loadMoreLink.textContent = 'select all';
            loadMoreLink.addEventListener('click', (e) => {
                e.preventDefault();
                loadAll();
            });
            selectAllLabel.innerHTML = '';
            selectAllLabel.appendChild(loadMoreLink);
        }
        else {
            selectAllLabel.innerHTML = 'select';
        }
    } else {
        selectAll.innerText = "add_box";
        selectAllLabel.innerHTML = 'select';
    }
}

function checkAll() {
    const items = listItems();
    Array.from(items).forEach(item => {
        const checkbox = item.querySelector('input[type="checkbox"]');
        checkbox.checked = true;
    });
}

function uncheckAll() {
    const items = listItems();
    Array.from(items).forEach(item => {
        const checkbox = item.querySelector('input[type="checkbox"]');
        checkbox.checked = false;
    });
}

function handleSelect(e) {
    e.preventDefault();
    const checkboxes = container.querySelectorAll('input[type="checkbox"]');
    const allChecked = Array.from(checkboxes).every(cb => cb.checked);
    
    checkboxes.forEach(cb => {
        cb.checked = !allChecked;
    });
    // checkAll();
    updateAction();
}

function getSelectedFids() {
    const checkedItems = container.querySelectorAll('li input[type="checkbox"]:checked');
    return Array.from(checkedItems).map(checkbox => {
        const li = checkbox.closest('li');
        return li.getAttribute('name');
    });
}

async function killAll() {
    const content = JSON.stringify({ fid: getSelectedFids() });
    // console.log("killAll", getSelectedFids(), content);
    const response = await fetch(`/outputs`, {
        method: 'DELETE',
        headers: {
            'Content-Type': 'application/json'
        },
        body: content
    });
}

function handleDelete(e) {
    e.preventDefault();
    const selectedItems = container.querySelectorAll('input[type="checkbox"]:checked');
    if (selectedItems.length > 0) {
        killDialog(
            "Kill and Delete", 
            `Are you sure you want to delete ${selectedItems.length} agentic exection${selectedItems.length > 1 ? 's' : ''}?`,
            "Yes",
            async () => { 
                await killAll() 
                updateAction();
            }
        );
    }
}
