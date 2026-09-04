const { defineConfig } = require('@vue/cli-service')
const YAML = require('yaml')
const fs = require("fs");
const path = require('path')
const configPath = path.resolve(__dirname, '../config.yaml')
const file = fs.readFileSync(configPath, 'utf8')
config = YAML.parse(file)

module.exports = defineConfig({
  transpileDependencies: true,
  devServer: {
    host: config["host"]["frontend"],
    port: config["port"]["frontend"], // 端口
    historyApiFallback: true,
  },
  
  // transpileDependencies: ['@arcgis']
})
