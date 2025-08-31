const BACKEND = "https://your-service.onrender.com"; // replace with actual Render domain

async function uploadApk() {
  const fileInput = document.getElementById("apkFile");
  if (fileInput.files.length === 0) {
    alert("Please select an APK file first.");
    return;
  }

  const formData = new FormData();
  formData.append("file", fileInput.files[0]);

  const res = await fetch(`${BACKEND}/upload`, {
    method: "POST",
    body: formData
  });

  const data = await res.json();
  document.getElementById("uploadStatus").innerText = data.message || data.error;
}

async function checkUpdate() {
  const res = await fetch(`${BACKEND}/check-update`);
  const data = await res.json();

  if (data.update) {
    document.getElementById("latestUpdate").innerHTML =
      `Latest APK: ${data.file} <br> 
       <a href="${BACKEND}/download/${data.file}" target="_blank">Download Link</a>`;
  } else {
    document.getElementById("latestUpdate").innerText = "No update available.";
  }
}
