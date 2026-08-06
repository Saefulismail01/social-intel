const TICKER_RE = /(?:^|[^A-Z0-9])\$([A-Z][A-Z0-9]{1,19})\b/gi;
const CASHTAG_RE = /(?:^|[^A-Za-z0-9])#([A-Za-z][A-Za-z0-9_]{0,39})\b/g;
const MENTION_RE = /(?:^|[^A-Za-z0-9])@([A-Za-z][A-Za-z0-9_.]{0,39})\b/g;

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

function coinPairs(value) {
  const output = [];
  if (!Array.isArray(value)) return output;
  for (const pair of value) {
    if (!pair || typeof pair !== "object") continue;
    for (const candidate of [pair.symbol, pair.pair]) {
      if (typeof candidate !== "string") continue;
      const normalized = candidate.toUpperCase().replaceAll("/", "").replaceAll("_", "").trim();
      if (/^[A-Z0-9]+$/.test(normalized) && !output.includes(normalized)) output.push(normalized);
    }
  }
  return output;
}

function count(item, names) {
  for (const name of names) if (Number.isFinite(item[name]) && item[name] >= 0) return Math.trunc(item[name]);
  return 0;
}

function ratio(value) {
  return Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
}

function tendency(value) {
  if (typeof value === "string") {
    const lowered = value.toLowerCase().trim();
    if (lowered === "bullish" || lowered === "bearish") return lowered;
  }
  return null;
}

function mediaUrls(item) {
  const urls = [];
  const media = item.media;
  if (!Array.isArray(media)) return urls;
  for (const entry of media) {
    let url;
    if (typeof entry === "string") url = entry.trim();
    else if (entry && typeof entry === "object") url = String(entry.url ?? entry.src ?? "").trim();
    if (url && !urls.includes(url)) urls.push(url);
  }
  return urls.slice(0, 50);
}

function timestamp(value, now) {
  let number = Number(value);
  if (!Number.isFinite(number)) return now.toISOString();
  if (number < 10_000_000_000) number *= 1000;
  return new Date(number).toISOString();
}

export function sanitizeFeed(payload, trackedSymbols, detectionPath = "feed-recommend", now = new Date()) {
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
    const publishedAt = timestamp(item.date, now);
    const detectedAt = now.toISOString();
    const pairs = [...coinPairs(item.tradingPairsV2), ...coinPairs(item.userInputTradingPairs)];
    posts.push({
      source_post_id: String(item.id), published_at: publishedAt, detected_at: detectedAt,
      author_id: String(item.squareAuthorId), author_name: String(item.authorName ?? ""),
      text, public_url: item.webLink ?? item.shareLink ?? null,
      share_url: typeof item.shareLink === "string" ? item.shareLink : null,
      symbols: selected,
      verification_type: Number.isInteger(item.authorVerificationType) ? item.authorVerificationType : null,
      engagement: { likes: count(item,["likeCount","likeCnt","likes"]), comments: count(item,["commentCount","commentCnt","comments"]), shares: count(item,["shareCount","shareCnt","shares"]), views: count(item,["viewCount","viewCnt","views","pageView"]) },
      card_type: String(item.cardType ?? ""), content_type: Number.isInteger(item.contentType) ? item.contentType : null,
      coin_pairs: [...new Set(pairs)], tendency: tendency(item.tendency),
      bullish_ratio: ratio(item.bullishRatio), bearish_ratio: ratio(item.bearishRatio),
      hashtags: [...text.matchAll(CASHTAG_RE)].map(m => m[1]),
      mentions: [...text.matchAll(MENTION_RE)].map(m => m[1]),
      is_reply: Boolean(item.isReply), parent_id: item.parentId ? String(item.parentId) : null,
      is_sticky: Boolean(item.isSticky), media_urls: mediaUrls(item),
      detection_path: detectionPath,
    });
  }
  return posts;
}
