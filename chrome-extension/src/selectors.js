/**
 * AitherHub - DOM Selectors Registry
 * 
 * All TikTok DOM selectors are centralized here for easy maintenance
 * when TikTok updates their UI structure.
 * 
 * Strategy: Multiple fallback selectors per element.
 * Priority: data-attribute > class-based > text-label > positional
 */

const SELECTORS = {
  // ══════════════════════════════════════════════════════════════
  // LIVE Streamer Page (shop.tiktok.com/streamer/live/...)
  // ══════════════════════════════════════════════════════════════
  live: {
    // Viewer count
    viewerCount: [
      '[class*="viewer"] [class*="count"]',
      '[class*="ViewerCount"]',
      '[data-e2e="viewer-count"]',
    ],

    // Comment list container
    commentList: [
      '[class*="comment-list"]',
      '[class*="CommentList"]',
      '[class*="chat-list"]',
      '[class*="ChatList"]',
    ],

    // Individual comment item
    commentItem: [
      '[class*="comment-item"]',
      '[class*="CommentItem"]',
      '[class*="chat-item"]',
      '[class*="ChatItem"]',
    ],

    // Comment username
    commentUser: [
      '[class*="comment-user"]',
      '[class*="nickname"]',
      '[class*="user-name"]',
    ],

    // Comment text
    commentText: [
      '[class*="comment-content"]',
      '[class*="comment-text"]',
      '[class*="message-content"]',
    ],

    // Product card / pinned product
    pinnedProduct: [
      '[class*="product-card"]',
      '[class*="ProductCard"]',
      '[class*="pinned-product"]',
      '[class*="PinnedProduct"]',
    ],

    // Product name in card
    productName: [
      '[class*="product-name"]',
      '[class*="ProductName"]',
      '[class*="product-title"]',
    ],

    // Product price
    productPrice: [
      '[class*="product-price"]',
      '[class*="ProductPrice"]',
      '[class*="price"]',
    ],

    // Purchase notification popup
    purchaseNotice: [
      '[class*="purchase-notice"]',
      '[class*="PurchaseNotice"]',
      '[class*="order-notice"]',
      '[class*="toast"]',
    ],

    // Live duration / timestamp
    liveDuration: [
      '[class*="duration"]',
      '[class*="Duration"]',
      '[class*="live-time"]',
      '[class*="timer"]',
    ],

    // Account name
    accountName: [
      '[class*="username"]',
      '[class*="account-name"]',
      'span[class*="Header"] span',
    ],
  },

  // ══════════════════════════════════════════════════════════════
  // Dashboard Page (shop.tiktok.com/workbench/live/overview)
  // ══════════════════════════════════════════════════════════════
  dashboard: {
    // KPI cards container
    kpiContainer: [
      '[class*="kpi"]',
      '[class*="overview-card"]',
      '[class*="OverviewCard"]',
      '[class*="metric-card"]',
    ],

    // Product table
    productTable: [
      'table',
      '[class*="product-table"]',
      '[class*="ProductTable"]',
      '[role="table"]',
    ],

    // Product table rows
    productRow: [
      'table tbody tr',
      '[class*="product-row"]',
      '[class*="table-row"]',
      '[role="row"]',
    ],

    // Trend graph container
    trendGraph: [
      '[class*="trend"]',
      '[class*="chart"]',
      '[class*="Chart"]',
      'canvas',
    ],

    // Tab navigation
    tabNav: [
      '[class*="tab"]',
      '[role="tab"]',
      '[class*="Tab"]',
    ],

    // Pagination
    pagination: [
      '[class*="pagination"]',
      '[class*="Pagination"]',
      '[class*="pager"]',
    ],
  },

  // ══════════════════════════════════════════════════════════════
  // Common / Shared
  // ══════════════════════════════════════════════════════════════
  common: {
    // Loading spinner
    loading: [
      '[class*="loading"]',
      '[class*="Loading"]',
      '[class*="spinner"]',
      '[class*="Spinner"]',
    ],

    // Modal / Dialog
    modal: [
      '[class*="modal"]',
      '[class*="Modal"]',
      '[role="dialog"]',
    ],

    // Toast / Notification
    toast: [
      '[class*="toast"]',
      '[class*="Toast"]',
      '[class*="notification"]',
      '[class*="Notification"]',
    ],
  },
};

/**
 * Query DOM with fallback selectors.
 * Tries each selector in order, returns first match.
 * @param {string[]} selectors - Array of CSS selectors to try
 * @param {Element} root - Root element to search within (default: document)
 * @returns {Element|null}
 */
function queryWithFallback(selectors, root = document) {
  for (const sel of selectors) {
    try {
      const el = root.querySelector(sel);
      if (el) return el;
    } catch (e) {
      // Invalid selector, skip
    }
  }
  return null;
}

/**
 * Query all matching elements with fallback selectors.
 * @param {string[]} selectors - Array of CSS selectors to try
 * @param {Element} root - Root element to search within
 * @returns {Element[]}
 */
function queryAllWithFallback(selectors, root = document) {
  for (const sel of selectors) {
    try {
      const els = root.querySelectorAll(sel);
      if (els.length > 0) return Array.from(els);
    } catch (e) {
      // Invalid selector, skip
    }
  }
  return [];
}

/**
 * Find element by text content (label-based fallback).
 * @param {string} text - Text to search for
 * @param {string} tag - HTML tag to search within (default: '*')
 * @param {Element} root - Root element
 * @returns {Element|null}
 */
function findByText(text, tag = '*', root = document) {
  const elements = root.querySelectorAll(tag);
  for (const el of elements) {
    if (el.textContent.trim().includes(text)) return el;
  }
  return null;
}

/**
 * Find the nearest numeric value near a label element.
 * Useful for extracting KPI values like "GMV: ¥82,075"
 * @param {string} labelText - Label text to find
 * @param {Element} root - Root element
 * @returns {string|null} - The numeric text found
 */
function findValueNearLabel(labelText, root = document) {
  const label = findByText(labelText, '*', root);
  if (!label) return null;

  // Check siblings
  const parent = label.parentElement;
  if (!parent) return null;

  // Look for a sibling or child with a number
  const candidates = parent.querySelectorAll('*');
  for (const el of candidates) {
    if (el === label) continue;
    const text = el.textContent.trim();
    // Match numbers with optional currency/comma/decimal
    if (/^[¥$€£]?\s*[\d,]+\.?\d*$/.test(text) || /^\d[\d,.]*$/.test(text)) {
      return text;
    }
  }

  return null;
}

// Export for use in content scripts
if (typeof globalThis !== 'undefined') {
  globalThis.AITHERHUB_SELECTORS = SELECTORS;
  globalThis.queryWithFallback = queryWithFallback;
  globalThis.queryAllWithFallback = queryAllWithFallback;
  globalThis.findByText = findByText;
  globalThis.findValueNearLabel = findValueNearLabel;
}
