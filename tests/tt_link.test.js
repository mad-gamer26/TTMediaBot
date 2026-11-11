// Run with: node tests/tt_link.test.js
// Checks the tt:// link parser in the web interface against the format used by
// BearWare's TeamTalk clients (qtTeamTalk MainWindow::parseArgs).
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "..", "bot", "web", "static", "app.js"), "utf8");
const start = src.indexOf("const TT_LINK_DEFAULTS");
const end = src.indexOf("async function showAdd()");
const { parseTeamTalkLink, suggestServerName } = new Function(
  src.slice(start, end) + "; return { parseTeamTalkLink, suggestServerName };")();

function parse(link) {
  const result = parseTeamTalkLink(link);
  return result.error ? result : Object.assign({ host: result.host }, result.values, { given: [...result.given], ignored: result.ignored });
}

// Everything given, as TeamTalk's "Generate TT URL" writes it.
let r = parse("tt://teamtalk.example.net?tcpport=7777&udpport=7777&encrypted=false&username=alice&password=p%40ss&channel=%2FHang%20out%2F&chanpasswd=c%26d");
assert.deepStrictEqual([r.host, r.tcpport, r.udpport, r.encrypted, r.username, r.password, r.channel, r.chanpasswd],
  ["teamtalk.example.net", 7777, 7777, false, "alice", "p@ss", "/Hang out/", "c&d"]);

// Nothing given: TeamTalk's defaults.
r = parse("tt://example.org");
assert.deepStrictEqual([r.tcpport, r.udpport, r.encrypted, r.username, r.password, r.channel, r.chanpasswd],
  [10333, 10333, false, "", "", "", ""]);
assert.deepStrictEqual(r.given, []);

// The UDP port does not follow the TCP port; it keeps its own default.
assert.strictEqual(parse("tt://h?tcpport=7777").udpport, 10333);

// Booleans, slashes, case, decoding.
assert.strictEqual(parse("tt://h?encrypted=1").encrypted, true);
assert.strictEqual(parse("tt://h?encrypted=TRUE").encrypted, true);
assert.strictEqual(parse("tt://h?encrypted=yes").encrypted, false);
assert.strictEqual(parse("TT:/example.org/?tcpport=10444").host, "example.org");
assert.strictEqual(parse("tt://h?password=a+b").password, "a+b");
assert.strictEqual(parse("tt://h?channel=%2FM%C3%BAsica%2F").channel, "/Música/");
assert.strictEqual(parse("tt://h?tcpport=1111&tcpport=2222").tcpport, 1111);

// Invalid ports fall back to the defaults; unknown parameters are reported.
r = parse("tt://h?tcpport=abc&udpport=70000&nickname=Bob");
assert.deepStrictEqual([r.tcpport, r.udpport, r.ignored], [10333, 10333, ["nickname"]]);

// A port written after the address is used as the TCP port unless tcpport is given.
r = parse("tt://h.example:7777");
assert.deepStrictEqual([r.host, r.tcpport], ["h.example", 7777]);
assert.strictEqual(parse("tt://h.example:7777?tcpport=7000").tcpport, 7000);

// Not links.
assert.ok(parse("https://example.org").error);
assert.ok(parse("tt://?tcpport=1").error);

// Suggested server names.
assert.strictEqual(suggestServerName("teamtalk.example.net", []), "example");
assert.strictEqual(suggestServerName("www.example.org", ["example"]), "example-2");
assert.strictEqual(suggestServerName("192.0.2.10", []), "server-192-0-2-10");

console.log("tt:// link tests passed");
