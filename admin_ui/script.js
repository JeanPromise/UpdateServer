async function fetchStats() {
  const res = await fetch("/stats");
  const data = await res.json();
  document.getElementById("users").textContent = data.users;
  document.getElementById("devices").textContent = data.devices;
  document.getElementById("last-upload").textContent = data.last_upload;
  return data;
}

// Upload form
document.getElementById("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const formData = new FormData(e.target);
  const res = await fetch("/upload", { method: "POST", body: formData });
  const msg = await res.json();
  alert(msg.message || "Upload failed");
  fetchStats();
});

// Growth chart
const ctx = document.getElementById("reportChart").getContext("2d");
const chart = new Chart(ctx, {
  type: "line",
  data: {
    labels: [],
    datasets: [{
      label: "Devices Installed",
      data: [],
      borderColor: "blue",
      fill: false
    }]
  }
});

setInterval(async () => {
  const stats = await fetchStats();
  chart.data.labels.push(new Date().toLocaleTimeString());
  chart.data.datasets[0].data.push(stats.devices);
  chart.update();
}, 5000); // update every 5s

fetchStats();
