document.addEventListener('DOMContentLoaded', (event) => {
    tabModes = document.querySelectorAll('.tab-mode');
    tabModes.forEach(tabMode => {
        tabMode.addEventListener('click', (event) => {
            const target = event.target;
            const ui = target.getAttribute('data-ui').substring(1);
            if (last_active != ui) {
                active = false;
            }
            else {
                active = true;
            }
            if (active) {
                follow[ui] = !follow[ui];
                if (follow[ui]) {
                    document.querySelector('#' + ui + '-follow').classList.add('fill');
                }
                else {
                    document.querySelector('#' + ui + '-follow').classList.remove('fill');
                }
            }
            if (follow[ui]) {
                scrollBottom();
            }
            last_active = ui;
        });
    });
    elmOutputArticle = document.getElementById('article-output');
    elmOutputArticle.addEventListener('click', () => {
        disableFollow('page-output');
    });
    elmStdioArticle = document.getElementById('article-stdio');
    elmStdioArticle.addEventListener('click', () => {
        disableFollow('page-stdio');
    });
    elmEventArticle = document.getElementById('article-event');
    elmEventArticle.addEventListener('click', () => {
        disableFollow('page-event');
    });
});

function scrollBottom() {
    if (follow["page-stdio"]) {
        elmStdioArticle.scrollTo(0, elmStdioArticle.scrollHeight);
    }
    if (follow["page-event"]) {
        elmEventArticle.scrollTo(0, elmEventArticle.scrollHeight);
    }
    if (follow["page-output"]) {
        const targetElement = document.getElementById('output-end');
        if (targetElement && elmOutputArticle) {
            const containerRect = elmOutputArticle.getBoundingClientRect();
            const targetRect = targetElement.getBoundingClientRect();
            const offsetRelativeToContainer = targetRect.top - containerRect.top;
            const newScrollTop = elmOutputArticle.scrollTop + offsetRelativeToContainer;
            elmOutputArticle.scrollTop = newScrollTop;
        } else {
             elmOutputArticle.scrollTo(0, elmOutputArticle.scrollHeight);
        }
    }
}

function scrollDown() {
    if (scrollDebounceTimer) {
        return;
    }
    scrollDebounceTimer = setTimeout(() => {
        scrollBottom();
        scrollDebounceTimer = null;
    }, scrollDebounceMs);
}

function disableFollow(key) {
    if (follow[key]) {
        follow[key] = false;
        const followIcon = document.querySelector('#' + key + '-follow');
        if (followIcon) {
            followIcon.classList.remove('fill');
        }
    }
}
