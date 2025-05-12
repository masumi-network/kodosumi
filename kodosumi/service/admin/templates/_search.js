let searchInput = null;
let debounceTimeout = null;
let closeIcon = null;

// Globale search Funktion, die von der Timeline überschrieben werden kann
function search() {
    if (searchInput.value) {
        closeIcon.style.display = 'inline';
    } else {
        closeIcon.style.display = 'none';
    }
}

function debouncedSearch() {
    if (searchInput.value) {
        closeIcon.style.display = 'inline';
    } else {
        closeIcon.style.display = 'none';
    }
    clearTimeout(debounceTimeout);
    debounceTimeout = setTimeout(search, 750);
}

function clearIcon() {
    searchInput.value = '';
    closeIcon.style.display = 'none';
    search();
    searchInput.focus();
}
