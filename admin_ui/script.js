const socket = io();

// Update device list on admin page
socket.on('update_devices', (devices) => {
    const list = document.getElementById('deviceList');
    list.innerHTML = '';
    for (let id in devices){
        const div = document.createElement('div');
        div.className = 'device';
        div.textContent = id + ' (version: ' + devices[id].version + ')';
        list.appendChild(div);
    }
});

// Optional messages from devices
socket.on('admin_message', data => console.log(data.message));

document.getElementById('sendUpdate').addEventListener('click', () => {
    const apkUrl = document.getElementById('apkUrl').value;
    if(!apkUrl) return alert("Enter APK URL!");
    fetch('/update', {
        method: 'POST',
        headers: { 'Content-Type':'application/x-www-form-urlencoded' },
        body: `apk_url=${encodeURIComponent(apkUrl)}`
    })
    .then(r => r.json())
    .then(res => alert(res.message))
    .catch(err => console.error(err));
});
