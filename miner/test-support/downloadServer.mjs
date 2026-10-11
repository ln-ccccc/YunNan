import express from 'express';
import { createProjectRoutes } from '../routes/projects.js';

const app = express();
app.use('/api/projects', createProjectRoutes());
const server = app.listen(0, '127.0.0.1', () => {
  process.stdout.write(`${server.address().port}\n`);
});
