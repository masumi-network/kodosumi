function formatDateTime(timestamp) {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    return date.toISOString().replace('T', ' ').slice(0, 19);
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
    str = str.replace(/\\s+/g, " ");
    str = str.replace(/: /g, "=");
    return str;
}

