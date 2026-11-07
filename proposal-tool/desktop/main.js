'use strict';
const { app, BrowserWindow, ipcMain, dialog, shell, Menu } = require('electron');
const fs = require('fs');
const path = require('path');

function createWindow() {
  const win = new BrowserWindow({
    width: 1180,
    height: 900,
    minWidth: 780,
    minHeight: 600,
    title: 'ASP Proposal Builder',
    backgroundColor: '#eef2f5',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  // Fail loudly if the UI file is missing/misplaced, instead of a blank window.
  const page = path.join(__dirname, 'renderer', 'index.html');
  if (!fs.existsSync(page)) {
    win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(
      '<body style="font:15px -apple-system,Segoe UI,Arial;padding:40px;color:#16242b">' +
      '<h2 style="color:#CF118C">Missing renderer/index.html</h2>' +
      '<p>The app could not find its user interface file. It must be at:</p>' +
      '<pre style="background:#f2f6f8;padding:12px;border-radius:8px">' + page + '</pre>' +
      '<p>Make sure <b>index.html</b> is inside a folder named <b>renderer</b>, next to main.js.</p></body>'));
    return;
  }
  win.loadFile(page);
  // Open external links (e.g. mailto:) in the OS default app, not a new window.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^(https?:|mailto:)/i.test(url)) { shell.openExternal(url); }
    return { action: 'deny' };
  });
}

// Native "Save As" — writes the generated file to disk. Returns the outcome.
ipcMain.handle('save-file', async (event, payload) => {
  const filename = (payload && payload.filename) || 'proposal.docx';
  const data = payload && payload.data;
  if (!data) return { status: 'error', message: 'no data' };
  const win = BrowserWindow.getFocusedWindow() || BrowserWindow.getAllWindows()[0];
  const { canceled, filePath } = await dialog.showSaveDialog(win, {
    defaultPath: filename,
    title: 'Save proposal'
  });
  if (canceled || !filePath) return { status: 'canceled' };
  await fs.promises.writeFile(filePath, Buffer.from(data));
  return { status: 'saved', path: filePath };
});

app.whenReady().then(() => {
  // Minimal, native menu (keeps Cmd+Q, copy/paste, etc.).
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { role: 'appMenu' },
    { role: 'editMenu' },
    { role: 'viewMenu' },
    { role: 'windowMenu' }
  ]));
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
