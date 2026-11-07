'use strict';
const { contextBridge, ipcRenderer } = require('electron');

// Exposes window.desktop.save(filename, ArrayBuffer) -> {status:'saved'|'canceled'|'error', path?}
contextBridge.exposeInMainWorld('desktop', {
  save: (filename, data) => ipcRenderer.invoke('save-file', { filename, data })
});
