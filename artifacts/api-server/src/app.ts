import express, { type Express } from "express";
import cors from "cors";
import { createProxyMiddleware } from "http-proxy-middleware";
import router from "./routes";

const app: Express = express();

app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use(
  "/legislation-api",
  createProxyMiddleware({
    target: "http://localhost:8001",
    changeOrigin: false,
    pathRewrite: { "^/legislation-api": "" },
  }),
);

app.use("/api", router);

export default app;
