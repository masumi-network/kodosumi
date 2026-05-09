function formatDateTime(timestamp) {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    const h = String(date.getHours()).padStart(2, '0');
    const min = String(date.getMinutes()).padStart(2, '0');
    const s = String(date.getSeconds()).padStart(2, '0');
    return `${y}-${m}-${d} ${h}:${min}:${s}`;
}

function formatRuntime(seconds) {
    if (!seconds) return '';
    seconds = Math.floor(seconds);
    const days = Math.floor(seconds / (24 * 3600));
    seconds = seconds % (24 * 3600);
    const hours = Math.floor(seconds / 3600);
    seconds = seconds % 3600;
    const minutes = Math.floor(seconds / 60);
    seconds = seconds % 60;
    if (days > 0) {
        return `${days}:${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
    } else if (hours > 0 || minutes >= 60) {
        return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
    } else if (minutes > 0 || seconds >= 60) {
        return `${minutes}:${seconds.toString().padStart(2, '0')}`;
    } else {
        return `${seconds}s`;
    }
}

function formatInputs(inputs) {
    let str = JSON.stringify(inputs);
    str = str.replace(/[\'\"\{\}\[\]]/g, "");
    str = str.replace(/\\r+/g, " ");
    str = str.replace(/\\n+/g, " ");
    str = str.replace(/\\s+/g, " ");
    str = str.replace(/:/g, ": ");
    if (str.length > 550) {
        str = str.substring(0, 550) + "...";
    }
    return str;
}

