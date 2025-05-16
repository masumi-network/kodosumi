let loadProgress = null;

document.addEventListener('DOMContentLoaded', (event) => {
    loadProgress = document.querySelector('#load-progress');
});

function showPulse() {
    loadProgress.style.display = 'block';
}

function hidePulse() {
    loadProgress.style.display = 'none';
}

