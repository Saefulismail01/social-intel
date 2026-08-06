const TICKER_RE = /(?:^|[^A-Z0-9])\$([A-Z][A-Z0-9]{1,19})\b/gi;

function pairSymbols(value) {
  const output = [];
  if (!Array.isArray(value)) return output;
  for (const pair of value) {
    if (!pair || typeof pair !== "object") continue;
    for (const candidate of [pair.symbol, pair.pair, pair.baseAsset, pair.token, pair.name, pair.ticker]) {
      if (typeof candidate !== "string") continue;
      let symbol = candidate.toUpperCase().replaceAll("/", "").replaceAll("_", "").trim();
      for (const quote of ["USDT", "USDC", "FDUSD", "BTC", "BNB"]) {
        if (symbol.endsWith(quote) && symbol.length > quote.length) { symbol = symbol.slice(0, -quote.length); break; }
      }
      if (/^[A-Z0-9]+$/.test(symbol) && !output.includes(symbol)) output.push(symbol);
    }
  }
  return output;
}

function count(item, names) {
  for (const name of names) if (Number.isFinite(item[name]) && item[name] >= 0) return Math.trunc(item[name]);
  return 0;
}

export function sanitizeFeed(payload, trackedSymbols, now = new Date()) {
  const tracked = new Set(trackedSymbols.map(value => value.toUpperCase().replace(/USDT$/, "")));
  const items = payload?.data?.vos;
  if (!Array.isArray(items)) return [];
  const posts = [];
  for (const item of items) {
    if (!item?.id || !item?.squareAuthorId) continue;
    const text = [item.title, item.subTitle].filter(value => typeof value === "string" && value.trim()).filter((v, i, a) => a.indexOf(v) === i).join("\n");
    const symbols = [...pairSymbols(item.tradingPairsV2), ...pairSymbols(item.userInputTradingPairs)];
    for (const match of text.matchAll(TICKER_RE)) if (!symbols.includes(match[1].toUpperCase())) symbols.push(match[1].toUpperCase());
    const selected = [...new Set(symbols.filter(symbol => tracked.has(symbol)))];
    if (!selected.length) continue;
    let timestamp = Number(item.date);
    if (timestamp < 10_000_000_000) timestamp *= 1000;
    const observed = Number.isFinite(timestamp) ? new Date(timestamp) : now;
    posts.push({
      source_post_id: String(item.id), observed_at: observed.toISOString(), author_id: String(item.squareAuthorId),
      author_name: String(item.authorName ?? ""), text, public_url: item.webLink ?? item.shareLink ?? null,
      symbols: selected, verification_type: Number.isInteger(item.authorVerificationType) ? item.authorVerificationType : null,
      engagement: { likes: count(item,["likeCount","likeCnt","likes"]), comments: count(item,["commentCount","commentCnt","comments"]), shares: count(item,["shareCount","shareCnt","shares"]), views: count(item,["viewCount","viewCnt","views","pageView"]) },
      card_type: String(item.cardType ?? ""), content_type: Number.isInteger(item.contentType) ? item.contentType : null,
    });
  }
  return posts;
}
