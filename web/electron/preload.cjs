const { contextBridge } = require('electron')

contextBridge.exposeInMainWorld('cometDesktop', {
  platform: process.platform,
})
