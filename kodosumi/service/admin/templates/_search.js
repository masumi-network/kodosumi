let searchInput = null;
let debounceTimeout = null;
let closeIcon = null;

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
