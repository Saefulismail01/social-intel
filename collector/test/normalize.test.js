import test from "node:test";
import assert from "node:assert/strict";
import { sanitizeFeed } from "../src/normalize.js";

test("sanitizes multi-token feed without request metadata", () => {
  const now = new Date();
  const rows = sanitizeFeed({data:{vos:[{id:"1",squareAuthorId:"a",authorName:"A",title:"$HOME and $BULLA",date:now.getTime(),tradingPairsV2:[{symbol:"HOMEUSDT"}],likeCount:2}]}}, ["HOME","BULLA"], now);
  assert.deepEqual(rows[0].symbols, ["HOME","BULLA"]);
  assert.equal(rows[0].engagement.likes, 2);
  assert.equal("cookie" in rows[0], false);
});

test("drops posts outside tracked universe", () => {
  const rows = sanitizeFeed({data:{vos:[{id:"1",squareAuthorId:"a",title:"$BTC",date:Date.now()}]}}, ["HOME"]);
  assert.equal(rows.length, 0);
});
