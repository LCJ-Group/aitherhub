import React, { useState, useMemo } from "react";
import { useTranslation } from 'react-i18next';

/**
 * AutoZoomPreview
 * ===============
 * Auto Zoom提案UI
 *
 * 画面収録は人物が小さいことが多い。
 * AIが検出した顔・商品の位置データ（face_region / product_region）を使い、
 * 自動ズーム提案を表示する。
 *
 * Props:
 *   autoZoomData  – [{ video_sec, face_region, product_region }]
 *   videoData     – 動画詳細オブジェクト
 *   onApplyZoom   – (zoomConfig) => void  ズーム設定をクリップに適用
 */

export default function AutoZoomPreview({ autoZoomData = [], videoData, onApplyZoom }) {
  useTranslation(); // triggers re-render on language change
  const [collapsed, setCollapsed] = useState(false);
  const [selectedTarget, setSelectedTarget] = useState("face"); // "face" | "product" | "auto"

  const formatTime = (seconds) => {
    if (seconds == null || isNaN(seconds)) return "--:--";
    const s = Math.round(Number(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${sec.toString().padStart(2, "0")}`;
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  // ズーム候補を計算
  const zoomCandidates = useMemo(() => {
    if (!autoZoomData || autoZoomData.length === 0) return [];

    return autoZoomData
      .filter((d) => {
        if (selectedTarget === "face") return d.face_region;
        if (selectedTarget === "product") return d.product_region;
        return d.face_region || d.product_region;
      })
      .map((d, i) => {
        const region = selectedTarget === "product"
          ? d.product_region
          : d.face_region || d.product_region;

        if (!region) return null;

        // ズーム倍率を計算（小さいほどズームが必要）
        const sizePct = region.size_pct || 10;
        const zoomLevel = sizePct < 5 ? 3.0 : sizePct < 10 ? 2.5 : sizePct < 20 ? 2.0 : 1.5;

        return {
          id: i + 1,
          video_sec: d.video_sec,
          target: d.face_region ? "face" : "product",
          x_pct: region.x_pct,
          y_pct: region.y_pct,
          size_pct: sizePct,
          zoom_level: zoomLevel,
          has_face: !!d.face_region,
          has_product: !!d.product_region,
        };
      })
      .filter(Boolean);
  }, [autoZoomData, selectedTarget]);

  if (!autoZoomData || autoZoomData.length === 0) return null;

  const faceCount = autoZoomData.filter((d) => d.face_region).length;
  const productCount = autoZoomData.filter((d) => d.product_region).length;

  return (
    <div className="mt-4">
      <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
        {/* ヘッダー */}
        <div className="px-5 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-emerald-500 to-teal-500 flex items-center justify-center">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="8"/>
                <line x1="21" y1="21" x2="16.65" y2="16.65"/>
                <line x1="11" y1="8" x2="11" y2="14"/>
                <line x1="8" y1="11" x2="14" y2="11"/>
              </svg>
            </div>
            <div>
              <h3 className="text-sm font-bold text-gray-800 flex items-center gap-2">
                Auto Zoom
                <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-600">
                  AI提案
                </span>
              </h3>
              <p className="text-xs text-gray-500 mt-0.5">
                顔・商品を自動検出してズーム提案
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={() => setCollapsed((s) => !s)}
            className="text-gray-400 p-2 rounded focus:outline-none transition-colors"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
              className={`w-5 h-5 transform transition-transform duration-200 ${!collapsed ? "rotate-180" : ""}`}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
        </div>

        {!collapsed && (
          <div className="px-5 pb-5">
            {/* 統計 */}
            <div className="flex items-center gap-3 px-3 py-2 rounded-xl bg-emerald-50 border border-emerald-100 text-xs mb-4">
              <span className="text-emerald-600 font-semibold">
                {autoZoomData.length} フレーム解析済
              </span>
              <span className="text-gray-400">|</span>
              <span className="text-gray-600">
                🎯 顔検出 {faceCount}件
              </span>
              <span className="text-gray-400">|</span>
              <span className="text-gray-600">
                📦 商品検出 {productCount}件
              </span>
            </div>

            {/* ターゲット選択 */}
            <div className="flex gap-2 mb-4">
              {[
                { key: "face", label: window.__t('auto_357', '🎯 顔にズーム'), desc: window.__t('auto_353', '配信者の顔を中心に') },
                { key: "product", label: window.__t('auto_361', '📦 商品にズーム'), desc: window.__t('auto_333', '商品を中心に') },
                { key: "auto", label: window.__t('auto_306', '✨ 自動選択'), desc: window.__t('auto_300', 'AIが最適な対象を選択') },
              ].map((opt) => (
                <button
                  key={opt.key}
                  type="button"
                  onClick={() => setSelectedTarget(opt.key)}
                  className={`flex-1 px-3 py-2 rounded-xl text-xs font-semibold transition-all ${
                    selectedTarget === opt.key
                      ? "bg-gradient-to-r from-emerald-500 to-teal-500 text-white shadow-md"
                      : "bg-gray-50 border border-gray-200 text-gray-600 hover:bg-gray-100"
                  }`}
                >
                  <div>{opt.label}</div>
                  <div className={`text-[10px] mt-0.5 ${selectedTarget === opt.key ? "text-white/70" : "text-gray-400"}`}>
                    {opt.desc}
                  </div>
                </button>
              ))}
            </div>

            {/* ズーム候補リスト */}
            {zoomCandidates.length > 0 ? (
              <div className="space-y-2">
                {zoomCandidates.slice(0, 10).map((zc) => (
                  <div
                    key={zc.id}
                    className="flex items-center justify-between px-3 py-2 rounded-lg border border-gray-200 hover:border-emerald-300 hover:bg-emerald-50/50 transition-all"
                  >
                    <div className="flex items-center gap-3">
                      <span className="text-xs font-mono text-gray-500 w-12">
                        {formatTime(zc.video_sec)}
                      </span>
                      <div className="flex items-center gap-1.5">
                        <span className="text-sm">{zc.target === "face" ? "🎯" : "📦"}</span>
                        <span className="text-xs text-gray-600">
                          位置: ({zc.x_pct}%, {zc.y_pct}%)
                        </span>
                        <span className="text-xs text-gray-400">
                          サイズ: {zc.size_pct}%
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${
                        zc.zoom_level >= 2.5
                          ? "bg-red-100 text-red-600"
                          : zc.zoom_level >= 2.0
                          ? "bg-amber-100 text-amber-600"
                          : "bg-green-100 text-green-600"
                      }`}>
                        {zc.zoom_level}x
                      </span>
                      {onApplyZoom && (
                        <button
                          type="button"
                          onClick={() => onApplyZoom({
                            video_sec: zc.video_sec,
                            target: zc.target,
                            x_pct: zc.x_pct,
                            y_pct: zc.y_pct,
                            zoom_level: zc.zoom_level,
                          })}
                          className="text-xs font-medium text-emerald-600 hover:text-emerald-700 px-2 py-1 rounded hover:bg-emerald-50 transition-colors"
                        >
                          適用
                        </button>
                      )}
                    </div>
                  </div>
                ))}
                {zoomCandidates.length > 10 && (
                  <div className="text-center text-xs text-gray-400 py-1">
                    他 {zoomCandidates.length - 10} 件のズーム候補
                  </div>
                )}
              </div>
            ) : (
              <div className="text-center py-4 text-gray-400 text-xs">
                選択したターゲットのズーム候補がありません
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
