import jwt from "jsonwebtoken";
import { config } from "./config.js";

const ALGORITHM = "HS256";

export function signAccessToken(payload) {
  return jwt.sign(payload, config.jwtSecret, { expiresIn: config.jwtExpiresIn, algorithm: ALGORITHM });
}

export function verifyAccessToken(token) {
  return jwt.verify(token, config.jwtSecret, { algorithms: [ALGORITHM] });
}
