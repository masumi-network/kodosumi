let loadProgress = null;

document.addEventListener('DOMContentLoaded', (event) => {
    loadProgress = document.querySelector('#load-progress');
    console.log("got it")
});

function showPulse() {
    loadProgress.style.display = 'block';
}

function hidePulse() {
    loadProgress.style.display = 'none';
}

