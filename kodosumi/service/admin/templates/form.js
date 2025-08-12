let activeUploads = new Map(); 
let currentBatchId = null;

async function initializeBatch() {
    try {
        const response = await fetch('/files/init_batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        const result = await response.json();
        currentBatchId = result.batch_id;
        console.log(`Initialized batch: ${currentBatchId}`);
        return currentBatchId;
    } catch (error) {
        currentBatchId = null;
        return null;
    }
}

async function cancelFile(name, upload_id) {
    const item = document.getElementById(upload_id);
    item.remove();
    if (!upload_id) return;
    try {
        let cancelRes =await fetch(`/files/cancel/${upload_id}`, {
            method: 'DELETE'
        });
        if (cancelRes.ok) {
            console.log(`Upload ${upload_id} of ${name} successfully cancelled`);
        } else {
            console.error(`Failed to cancel upload: ${cancelRes.status}`);
        }
        localStorage.removeItem(upload_id);
        activeUploads.delete(upload_id);
        await cancelRes.text();
        updateForm(name);
    } catch (error) {
        console.error('Error cancelling upload:', error);
    }
}

function addProgressBar(name, relPath) {
    const container = document.getElementById(`_items-${name}`);
    const div = document.createElement('div');
    div.className = 'file-item';
    div.innerHTML = `
        <i>cancel</i> ${relPath}<br>
        <progress id="${name}-${relPath}-progress" value="0" max="100"></progress> 
    `;
    container.appendChild(div);
    return div;
}

async function uploadFile(file, relativePath, onProgress) {
    const chunkSize = 1 * 1024 * 1024; // 5 MB
    const totalChunks = Math.ceil(file.size / chunkSize);
    const parallelUploads = 4;
    const startTime = Date.now();

    const initRes = await fetch('/files/init', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
            filename: relativePath, 
            total_chunks: totalChunks,
            batch_id: currentBatchId 
        })
    });
    if (!initRes.ok) {
        throw new Error(`Failed to initialize upload: ${initRes.status}`);
    }
    const { upload_id, batch_id } = await initRes.json();
    activeUploads.set(upload_id, { 
        filename: relativePath, 
        status: 'uploading',
        progressDiv: null,
        totalChunks,
        uploadedChunks: 0,
        batchId: batch_id
    });
    let completedChunks = JSON.parse(localStorage.getItem(upload_id)) || [];
    let uploadedBytes = completedChunks.length * chunkSize;

    async function uploadChunk(i, trial=1) {
        if (trial > 5) {
            throw(`Stop retrying chunk ${i}:`);
        }
        if (completedChunks.includes(i)) return;

        const start = i * chunkSize;
        const end = Math.min(file.size, start + chunkSize);
        const chunkBlob = file.slice(start, end);

        const form = new FormData();
        form.append('upload_id', upload_id);
        form.append('chunk_number', i);
        form.append('chunk', chunkBlob);

        try {
            const res = await fetch('/files/chunk', { method: 'POST', body: form });
            if (!res.ok) {
                throw new Error(`Chunk upload failed: ${res.status}`);
            }            
            const data = await res.json();
            if (data.status === "chunk received") {
                completedChunks.push(i);
                localStorage.setItem(upload_id, JSON.stringify(completedChunks));
                uploadedBytes += (end - start);
                const percent = Math.min((uploadedBytes / file.size) * 100, 100);
                const elapsedSec = (Date.now() - startTime) / 1000;
                const speed = (uploadedBytes / (1024 * 1024)) / elapsedSec;
                const uploadInfo = activeUploads.get(upload_id);
                if (uploadInfo) {
                    uploadInfo.uploadedChunks = completedChunks.length;
                }
                if (onProgress) {
                    onProgress({
                        percent: percent.toFixed(2),
                        uploaded: uploadedBytes,
                        total: file.size,
                        speed: speed.toFixed(2)
                    });
                }
            }
        } catch (error) {
            console.warn(`Retrying chunk ${i}:`, error);
            return uploadChunk(i, trial + 1);
        }
    }
    const chunkIndices = Array.from({ length: totalChunks }, (_, i) => i);
    for (let i = 0; i < chunkIndices.length; i += parallelUploads) {
        const batch = chunkIndices.slice(i, i + parallelUploads);
        await Promise.all(batch.map(uploadChunk));
    }
    const uploadInfo = activeUploads.get(upload_id);
    if (uploadInfo) {
        uploadInfo.status = 'ready';
    }
    return upload_id;
}

async function uploadMultiple(name, files) {
    if (!currentBatchId) {
        await initializeBatch();
    }
    document.getElementById(`_files-${name}`).style.display = 'block';
    for (const file of files) {
        const relativePath = file.webkitRelativePath || file.name;
        const progressDiv = addProgressBar(name, relativePath);
        const progressBar = progressDiv.querySelector('progress');
        const fileId = progressDiv.querySelector('i');
        const upload_id = await uploadFile(file, relativePath, (progress) => {
            progressBar.value = progress.percent;
        });
        progressDiv.id = upload_id;
        fileId.addEventListener('click', () => {
            cancelFile(name, upload_id);
        });
        progressBar.remove();
    }
    updateForm(name);
}

async function updateForm(name) {
    // console.log("updating", name);
    const readyUploads = Array.from(activeUploads.entries())
        .filter(([_, info]) => info.status === 'ready')
        .map(([upload_id, info]) => [upload_id, { 
            filename: info.filename, 
            totalChunks: info.totalChunks
        }]);
    const listInput = document.getElementById(`_list-${name}`);
    const data = {
        "batchId": currentBatchId,
        "items": Object.fromEntries(readyUploads),
        "name": name
    }
    listInput.value = JSON.stringify(data);
}

document.addEventListener('DOMContentLoaded', () => {
    Array.from(document.getElementsByClassName('fileInput')).forEach(input => {
        input.addEventListener('change', (e) => {
            const name = input.id.match(/^(_[^-]+-)(.+)/)[2];
            uploadMultiple(name, e.target.files);
        });
    });
});
