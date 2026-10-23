import assert from "node:assert/strict";
import { test } from "node:test";
import { ConnectionCodeError, originPattern, parseConnectionCode, wsUrl } from "../src/connect.js";

test("домен:код и варианты записи", () => {
  assert.deepEqual(parseConnectionCode("solver.example.com:abcd-efgh"), { url: "https://solver.example.com", code: "ABCD-EFGH" });
  assert.deepEqual(parseConnectionCode("  `solver.example.com:ABCDEFGH`\n"), { url: "https://solver.example.com", code: "ABCD-EFGH" });
  assert.equal(parseConnectionCode("https://solver.example.com:ABCD-EFGH").url, "https://solver.example.com");
  assert.equal(parseConnectionCode("solver.example.com:8443:ABCD-EFGH").url, "https://solver.example.com:8443");
  assert.deepEqual(parseConnectionCode("abcdefgh"), { url: null, code: "ABCD-EFGH" });
  assert.equal(parseConnectionCode("127.0.0.1:8080:ABCD-EFGH").url, "http://127.0.0.1:8080");
  assert.equal(parseConnectionCode("localhost:ABCD-EFGH").url, "http://localhost");
});

test("прежний формат yk1", () => {
  const b64 = Buffer.from("https://solver.example.com").toString("base64url");
  assert.deepEqual(parseConnectionCode(`yk1.${b64}.ABCD-EFGH`), { url: "https://solver.example.com", code: "ABCD-EFGH" });
});

test("неверные коды отклоняются", () => {
  for (const bad of ["", "yk1.", "yk1.!!!.ABCD-EFGH", "ABCD", "https://x.com", "solver.example.com:12",
                     "solver.example.com:ABCD-EFG", "exa_mple.com:ABCD-EFGH", "solver.example.com/path:ABCD-EFGH",
                     "user@solver.example.com:ABCD-EFGH"]) {
    assert.throws(() => parseConnectionCode(bad), ConnectionCodeError, bad);
  }
  assert.throws(() => parseConnectionCode("http://evil.example.com:ABCD-EFGH"), /https/);
});

test("маска разрешения и адрес WebSocket", () => {
  assert.equal(originPattern("https://solver.example.com:8443"), "https://solver.example.com/*");
  assert.equal(originPattern("http://127.0.0.1:8080"), "http://127.0.0.1/*");
  assert.equal(wsUrl("https://solver.example.com"), "wss://solver.example.com/v1/agent");
  assert.equal(wsUrl("http://127.0.0.1:8080"), "ws://127.0.0.1:8080/v1/agent");
});
