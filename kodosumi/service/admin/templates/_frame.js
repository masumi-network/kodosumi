let circle = null;

document.addEventListener('DOMContentLoaded', (event) => {
    circle = document.querySelector('.pulse-circle');
});

function showPulse() {
    circle.style.display = 'block';
    circle.classList.add('visible');
    circle.classList.remove('hidden');
}

function hidePulse() {
    circle.style.display = 'none';
    circle.classList.add("hidden");
    circle.classList.remove('visible');
}

