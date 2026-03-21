import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Plus,
  Trash2,
  Edit3,
  Loader2,
  CheckCircle,
  AlertCircle,
  Brain,
  Zap,
  Video,
  Tag,
  Play,
  Clock,
  ChevronDown,
  ChevronUp,
  X,
  Save,
  User,
  Sparkles,
  Database,
  Settings,
  MessageSquare,
  Send,
  FileText,
  Copy,
} from "lucide-react";
import personaService from "../base/services/personaService";

const API_BASE = import.meta.env.VITE_API_URL || "";
const ADMIN_KEY = "aither:hub";

/**
 * PersonaPage — Liver Clone (Persona) Management
 *
 * Features:
 *   - Create/Edit/Delete personas
 *   - Tag videos for training data
 *   - Preview dataset
 *   - Start fine-tuning
 *   - Monitor training status
 */
export default function PersonaPage() {
  const navigate = useNavigate();

  // ── State ──
  const [personas, setPersonas] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedPersona, setSelectedPersona] = useState(null);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [showVideoTagger, setShowVideoTagger] = useState(false);
  const [datasetPreview, setDatasetPreview] = useState(null);
  const [loadingDataset, setLoadingDataset] = useState(false);
  const [trainingStatus, setTrainingStatus] = useState(null);

  // ── Create/Edit Form ──
  const [formData, setFormData] = useState({
    name: "",
    description: "",
    speaking_style: "",
    catchphrases: "",
    personality_traits: "",
    voice_id: "",
    language: "ja",
  });
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState(null);

  // ── Videos for Tagging ──
  const [allVideos, setAllVideos] = useState([]);
  const [taggedVideoIds, setTaggedVideoIds] = useState(new Set());
  const [loadingVideos, setLoadingVideos] = useState(false);
  const [videoSearchQuery, setVideoSearchQuery] = useState("");
  const [videoPage, setVideoPage] = useState(0);
  const [videoTotal, setVideoTotal] = useState(0);
  const VIDEO_PAGE_SIZE = 50;

  // ── Chat State ──
  const [showChat, setShowChat] = useState(false);
  const [chatMessages, setChatMessages] = useState([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [chatContext, setChatContext] = useState("");

  // ── Script Generation State ──
  const [showScriptGen, setShowScriptGen] = useState(false);
  const [scriptProducts, setScriptProducts] = useState("");
  const [scriptDuration, setScriptDuration] = useState(5);
  const [scriptStyle, setScriptStyle] = useState("");
  const [scriptNotes, setScriptNotes] = useState("");
  const [generatedScript, setGeneratedScript] = useState(null);
  const [scriptLoading, setScriptLoading] = useState(false);

  // ── Load Personas ──
  const loadPersonas = useCallback(async () => {
    try {
      setLoading(true);
      const data = await personaService.listPersonas();
      setPersonas(data.personas || []);
    } catch (err) {
      console.error("Failed to load personas:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPersonas();
  }, [loadPersonas]);

  // ── Load Videos for Tagging (using available-videos endpoint) ──
  const loadVideos = useCallback(async (page = 0, search = "") => {
    if (!selectedPersona) return;
    setLoadingVideos(true);
    try {
      const params = new URLSearchParams({
        limit: VIDEO_PAGE_SIZE,
        offset: page * VIDEO_PAGE_SIZE,
      });
      if (search.trim()) params.append("search", search.trim());

      const res = await fetch(
        `${API_BASE}/api/v1/personas/${selectedPersona.id}/available-videos?${params}`,
        { headers: { "X-Admin-Key": ADMIN_KEY } }
      );
      const data = await res.json();
      setAllVideos(data.videos || []);
      setVideoTotal(data.total || 0);
      setVideoPage(page);
    } catch (err) {
      console.error("Failed to load videos:", err);
    } finally {
      setLoadingVideos(false);
    }
  }, [selectedPersona]);

  // ── Load Dataset Preview ──
  const loadDatasetPreview = async (personaId) => {
    setLoadingDataset(true);
    try {
      const data = await personaService.getDatasetPreview(personaId);
      setDatasetPreview(data);
    } catch (err) {
      console.error("Failed to load dataset preview:", err);
      setDatasetPreview(null);
    } finally {
      setLoadingDataset(false);
    }
  };

  // ── Load Training Status ──
  const loadTrainingStatus = async (personaId) => {
    try {
      const data = await personaService.getTrainingStatus(personaId);
      setTrainingStatus(data);
    } catch (err) {
      console.error("Failed to load training status:", err);
    }
  };

  // ── Select Persona (loads detail from API) ──
  const handleSelectPersona = async (persona) => {
    setSelectedPersona(persona);
    setShowVideoTagger(false);
    setDatasetPreview(null);
    setTrainingStatus(null);
    setAllVideos([]);

    // Load persona detail to get tagged_videos
    try {
      const detail = await personaService.getPersona(persona.id);
      const taggedVids = detail.tagged_videos || [];
      const taggedIds = new Set(taggedVids.map((v) => String(v.video_id)));
      setTaggedVideoIds(taggedIds);

      // Update selectedPersona with full detail
      setSelectedPersona({ ...persona, ...detail.persona, _tagged_videos: taggedVids });
    } catch (err) {
      console.error("Failed to load persona detail:", err);
      setTaggedVideoIds(new Set());
    }

    // Load dataset preview and training status
    await Promise.all([
      loadDatasetPreview(persona.id),
      loadTrainingStatus(persona.id),
    ]);
  };

  // ── Create/Update Persona ──
  const handleSavePersona = async () => {
    setSaving(true);
    try {
      const payload = {
        name: formData.name,
        description: formData.description,
        speaking_style: formData.speaking_style,
        catchphrases: formData.catchphrases
          ? formData.catchphrases.split(",").map((s) => s.trim()).filter(Boolean)
          : [],
        personality_traits: formData.personality_traits
          ? formData.personality_traits.split(",").map((s) => s.trim()).filter(Boolean)
          : [],
        voice_id: formData.voice_id || null,
        language: formData.language,
      };

      if (editingId) {
        await personaService.updatePersona(editingId, payload);
      } else {
        await personaService.createPersona(payload);
      }

      setShowCreateForm(false);
      setEditingId(null);
      setFormData({ name: "", description: "", speaking_style: "", catchphrases: "", personality_traits: "", voice_id: "", language: "ja" });
      await loadPersonas();
    } catch (err) {
      console.error("Failed to save persona:", err);
      alert(`Error: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  // ── Delete Persona ──
  const handleDeletePersona = async (id) => {
    if (!confirm("Delete this persona? This cannot be undone.")) return;
    try {
      await personaService.deletePersona(id);
      if (selectedPersona?.id === id) setSelectedPersona(null);
      await loadPersonas();
    } catch (err) {
      console.error("Failed to delete persona:", err);
    }
  };

  // ── Edit Persona ──
  const handleEditPersona = (persona) => {
    setFormData({
      name: persona.name || "",
      description: persona.description || "",
      speaking_style: persona.speaking_style || "",
      catchphrases: (persona.catchphrases || []).join(", "),
      personality_traits: (persona.personality_traits || []).join(", "),
      voice_id: persona.voice_id || "",
      language: persona.language || "ja",
    });
    setEditingId(persona.id);
    setShowCreateForm(true);
  };

  // ── Toggle Video Tag ──
  const handleToggleVideoTag = async (videoId) => {
    if (!selectedPersona) return;
    const id = String(videoId);

    try {
      if (taggedVideoIds.has(id)) {
        await personaService.untagVideos(selectedPersona.id, [id]);
        setTaggedVideoIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      } else {
        await personaService.tagVideos(selectedPersona.id, [id]);
        setTaggedVideoIds((prev) => new Set([...prev, id]));
      }
      // Refresh dataset preview after tag change
      loadDatasetPreview(selectedPersona.id);
    } catch (err) {
      console.error("Failed to toggle video tag:", err);
      alert(`Error: ${err.message}`);
    }
  };

  // ── Start Training ──
  const handleStartTraining = async () => {
    if (!selectedPersona) return;
    if (!confirm(`Start fine-tuning for "${selectedPersona.name}"? This will use OpenAI API credits.`)) return;

    try {
      await personaService.startTraining(selectedPersona.id, {
        base_model: "gpt-4.1-mini",
        n_epochs: 3,
      });
      alert("Training started! This may take 30-60 minutes.");
      await loadTrainingStatus(selectedPersona.id);
    } catch (err) {
      console.error("Failed to start training:", err);
      alert(`Error: ${err.message}`);
    }
  };

  // ── Chat Functions ──
  const handleSendChat = async () => {
    if (!chatInput.trim() || !selectedPersona || chatLoading) return;
    const userMsg = chatInput.trim();
    setChatInput("");
    setChatMessages((prev) => [...prev, { role: "user", content: userMsg }]);
    setChatLoading(true);
    try {
      const history = chatMessages.map((m) => ({ role: m.role, content: m.content }));
      const data = await personaService.chat(
        selectedPersona.id,
        userMsg,
        chatContext || null,
        history
      );
      setChatMessages((prev) => [
        ...prev,
        { role: "assistant", content: data.response, usage: data.usage },
      ]);
    } catch (err) {
      console.error("Chat error:", err);
      setChatMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Error: ${err.message}`, error: true },
      ]);
    } finally {
      setChatLoading(false);
    }
  };

  // ── Script Generation ──
  const handleGenerateScript = async () => {
    if (!selectedPersona || scriptLoading) return;
    setScriptLoading(true);
    setGeneratedScript(null);
    try {
      const products = scriptProducts
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      const data = await personaService.generateScript(selectedPersona.id, {
        products,
        duration_minutes: scriptDuration,
        style: scriptStyle || undefined,
        notes: scriptNotes || undefined,
      });
      setGeneratedScript(data);
    } catch (err) {
      console.error("Script generation error:", err);
      alert(`Error: ${err.message}`);
    } finally {
      setScriptLoading(false);
    }
  };

  const handleCopyScript = () => {
    if (generatedScript?.script) {
      navigator.clipboard.writeText(generatedScript.script);
    }
  };

  // ── Video Search with debounce ──
  useEffect(() => {
    if (!showVideoTagger || !selectedPersona) return;
    const timer = setTimeout(() => {
      loadVideos(0, videoSearchQuery);
    }, 300);
    return () => clearTimeout(timer);
  }, [videoSearchQuery, showVideoTagger, selectedPersona]);

  // ── Status Badge ──
  const StatusBadge = ({ status }) => {
    const colors = {
      ready: "bg-gray-100 text-gray-600",
      none: "bg-gray-100 text-gray-600",
      preparing: "bg-yellow-100 text-yellow-700",
      training: "bg-blue-100 text-blue-700",
      completed: "bg-green-100 text-green-700",
      failed: "bg-red-100 text-red-700",
    };
    return (
      <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${colors[status] || colors.ready}`}>
        {status || "ready"}
      </span>
    );
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 via-purple-900 to-gray-900 text-white">
      {/* Header */}
      <div className="bg-black/30 border-b border-white/10 px-6 py-4">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={() => navigate("/")}
              className="p-2 hover:bg-white/10 rounded-lg transition-colors"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div>
              <h1 className="text-xl font-bold flex items-center gap-2">
                <Brain className="w-6 h-6 text-purple-400" />
                Liver Clone Manager
              </h1>
              <p className="text-xs text-gray-400 mt-0.5">
                Create AI clones of your livers by training on their past streams
              </p>
            </div>
          </div>
          <button
            onClick={() => {
              setShowCreateForm(true);
              setEditingId(null);
              setFormData({ name: "", description: "", speaking_style: "", catchphrases: "", personality_traits: "", voice_id: "", language: "ja" });
            }}
            className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded-lg text-sm font-medium transition-colors"
          >
            <Plus className="w-4 h-4" />
            New Persona
          </button>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-6 py-6">
        <div className="grid grid-cols-12 gap-6">
          {/* ── Left: Persona List ── */}
          <div className="col-span-4">
            <div className="bg-white/5 rounded-xl border border-white/10 overflow-hidden">
              <div className="p-4 border-b border-white/10">
                <h2 className="text-sm font-bold text-gray-200 flex items-center gap-2">
                  <User className="w-4 h-4 text-purple-400" />
                  Personas ({personas.length})
                </h2>
              </div>

              {loading ? (
                <div className="flex items-center justify-center py-12">
                  <Loader2 className="w-6 h-6 animate-spin text-purple-400" />
                </div>
              ) : personas.length === 0 ? (
                <div className="text-center py-12 px-4">
                  <Brain className="w-12 h-12 mx-auto mb-3 text-gray-600" />
                  <p className="text-sm text-gray-400">No personas yet</p>
                  <p className="text-xs text-gray-500 mt-1">Create a persona to start training an AI clone</p>
                </div>
              ) : (
                <div className="divide-y divide-white/5">
                  {personas.map((p) => (
                    <div
                      key={p.id}
                      onClick={() => handleSelectPersona(p)}
                      className={`p-4 cursor-pointer transition-colors hover:bg-white/5 ${
                        selectedPersona?.id === p.id ? "bg-purple-500/10 border-l-2 border-purple-500" : ""
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <h3 className="text-sm font-bold text-gray-200 truncate">{p.name}</h3>
                            <StatusBadge status={p.finetune_status} />
                          </div>
                          <p className="text-[10px] text-gray-500 mt-0.5 truncate">{p.description || "No description"}</p>
                          <div className="flex items-center gap-3 mt-1.5">
                            <span className="text-[10px] text-gray-500 flex items-center gap-1">
                              <Video className="w-3 h-3" />
                              {p.tagged_video_count || 0} videos
                            </span>
                            <span className="text-[10px] text-gray-500 flex items-center gap-1">
                              <Database className="w-3 h-3" />
                              {p.language || "ja"}
                            </span>
                          </div>
                        </div>
                        <div className="flex items-center gap-1 ml-2">
                          <button
                            onClick={(e) => { e.stopPropagation(); handleEditPersona(p); }}
                            className="p-1.5 hover:bg-white/10 rounded-lg transition-colors"
                          >
                            <Edit3 className="w-3.5 h-3.5 text-gray-400" />
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); handleDeletePersona(p.id); }}
                            className="p-1.5 hover:bg-red-500/20 rounded-lg transition-colors"
                          >
                            <Trash2 className="w-3.5 h-3.5 text-red-400" />
                          </button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* ── Right: Persona Details ── */}
          <div className="col-span-8">
            {!selectedPersona ? (
              <div className="bg-white/5 rounded-xl border border-white/10 p-12 text-center">
                <Brain className="w-16 h-16 mx-auto mb-4 text-gray-600" />
                <h3 className="text-lg font-bold text-gray-400">Select a Persona</h3>
                <p className="text-sm text-gray-500 mt-2">
                  Choose a persona from the list to manage training data and start fine-tuning
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                {/* Persona Info Card */}
                <div className="bg-white/5 rounded-xl border border-white/10 p-5">
                  <div className="flex items-start justify-between">
                    <div>
                      <h2 className="text-lg font-bold text-gray-100 flex items-center gap-2">
                        {selectedPersona.name}
                        <StatusBadge status={selectedPersona.finetune_status} />
                      </h2>
                      <p className="text-xs text-gray-400 mt-1">{selectedPersona.description || "No description"}</p>
                    </div>
                    {selectedPersona.finetune_model_id && (
                      <div className="bg-green-500/10 border border-green-500/30 rounded-lg px-3 py-1.5">
                        <p className="text-[10px] text-green-400 font-medium">Model Ready</p>
                        <p className="text-[9px] text-green-500/70 font-mono truncate max-w-[200px]">
                          {selectedPersona.finetune_model_id}
                        </p>
                      </div>
                    )}
                  </div>

                  {/* Persona Details */}
                  <div className="grid grid-cols-2 gap-4 mt-4">
                    <div>
                      <p className="text-[10px] text-gray-500 mb-1">Speaking Style</p>
                      <p className="text-xs text-gray-300">{selectedPersona.speaking_style || selectedPersona.style_prompt || "Not set"}</p>
                    </div>
                    <div>
                      <p className="text-[10px] text-gray-500 mb-1">Language</p>
                      <p className="text-xs text-gray-300">{selectedPersona.language || "ja"}</p>
                    </div>
                    <div>
                      <p className="text-[10px] text-gray-500 mb-1">Catchphrases</p>
                      <div className="flex flex-wrap gap-1">
                        {(selectedPersona.catchphrases || []).length > 0 ? (
                          selectedPersona.catchphrases.map((c, i) => (
                            <span key={i} className="px-2 py-0.5 bg-purple-500/20 text-purple-300 rounded-full text-[10px]">
                              {c}
                            </span>
                          ))
                        ) : (
                          <span className="text-xs text-gray-500">None</span>
                        )}
                      </div>
                    </div>
                    <div>
                      <p className="text-[10px] text-gray-500 mb-1">Personality Traits</p>
                      <div className="flex flex-wrap gap-1">
                        {(selectedPersona.personality_traits || []).length > 0 ? (
                          selectedPersona.personality_traits.map((t, i) => (
                            <span key={i} className="px-2 py-0.5 bg-blue-500/20 text-blue-300 rounded-full text-[10px]">
                              {t}
                            </span>
                          ))
                        ) : (
                          <span className="text-xs text-gray-500">None</span>
                        )}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Video Tagging Section */}
                <div className="bg-white/5 rounded-xl border border-white/10 overflow-hidden">
                  <button
                    onClick={() => {
                      const newState = !showVideoTagger;
                      setShowVideoTagger(newState);
                      if (newState && allVideos.length === 0) loadVideos(0, "");
                    }}
                    className="w-full p-4 flex items-center justify-between hover:bg-white/5 transition-colors"
                  >
                    <div className="flex items-center gap-2">
                      <Tag className="w-4 h-4 text-cyan-400" />
                      <h3 className="text-sm font-bold text-gray-200">
                        Training Videos ({taggedVideoIds.size} tagged)
                      </h3>
                    </div>
                    {showVideoTagger ? (
                      <ChevronUp className="w-4 h-4 text-gray-400" />
                    ) : (
                      <ChevronDown className="w-4 h-4 text-gray-400" />
                    )}
                  </button>

                  {showVideoTagger && (
                    <div className="border-t border-white/10 p-4">
                      {/* Search */}
                      <input
                        type="text"
                        value={videoSearchQuery}
                        onChange={(e) => setVideoSearchQuery(e.target.value)}
                        placeholder="Search videos by filename..."
                        className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-sm text-gray-200 placeholder-gray-500 mb-3"
                      />

                      {loadingVideos ? (
                        <div className="flex items-center justify-center py-8">
                          <Loader2 className="w-5 h-5 animate-spin text-purple-400" />
                        </div>
                      ) : (
                        <>
                          <div className="max-h-80 overflow-y-auto space-y-1">
                            {allVideos.map((v) => {
                              const isTagged = v.is_tagged || taggedVideoIds.has(String(v.id));
                              return (
                                <div
                                  key={v.id}
                                  onClick={() => handleToggleVideoTag(v.id)}
                                  className={`flex items-center gap-3 p-2.5 rounded-lg cursor-pointer transition-colors ${
                                    isTagged
                                      ? "bg-purple-500/10 border border-purple-500/30"
                                      : "hover:bg-white/5 border border-transparent"
                                  }`}
                                >
                                  <div className={`w-5 h-5 rounded flex items-center justify-center flex-shrink-0 ${
                                    isTagged ? "bg-purple-500" : "bg-white/10"
                                  }`}>
                                    {isTagged && <CheckCircle className="w-3.5 h-3.5 text-white" />}
                                  </div>
                                  <div className="min-w-0 flex-1">
                                    <p className="text-xs text-gray-200 truncate">{v.filename || v.original_filename || `Video ${v.id?.substring(0, 8)}`}</p>
                                    <div className="flex items-center gap-2 mt-0.5">
                                      <span className="text-[10px] text-gray-500">
                                        {v.segment_count || 0} segments
                                      </span>
                                      {v.created_at && (
                                        <span className="text-[10px] text-gray-500">
                                          {new Date(v.created_at).toLocaleDateString()}
                                        </span>
                                      )}
                                    </div>
                                  </div>
                                </div>
                              );
                            })}
                          </div>

                          {/* Pagination */}
                          {videoTotal > VIDEO_PAGE_SIZE && (
                            <div className="flex items-center justify-between mt-3 pt-3 border-t border-white/10">
                              <span className="text-[10px] text-gray-500">
                                {videoPage * VIDEO_PAGE_SIZE + 1}-{Math.min((videoPage + 1) * VIDEO_PAGE_SIZE, videoTotal)} of {videoTotal}
                              </span>
                              <div className="flex gap-2">
                                <button
                                  onClick={() => loadVideos(videoPage - 1, videoSearchQuery)}
                                  disabled={videoPage === 0}
                                  className="px-3 py-1 text-xs bg-white/5 rounded-lg hover:bg-white/10 disabled:opacity-30 disabled:cursor-not-allowed"
                                >
                                  Prev
                                </button>
                                <button
                                  onClick={() => loadVideos(videoPage + 1, videoSearchQuery)}
                                  disabled={(videoPage + 1) * VIDEO_PAGE_SIZE >= videoTotal}
                                  className="px-3 py-1 text-xs bg-white/5 rounded-lg hover:bg-white/10 disabled:opacity-30 disabled:cursor-not-allowed"
                                >
                                  Next
                                </button>
                              </div>
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </div>

                {/* Dataset Preview */}
                <div className="bg-white/5 rounded-xl border border-white/10 p-5">
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-bold text-gray-200 flex items-center gap-2">
                      <Database className="w-4 h-4 text-green-400" />
                      Dataset Preview
                    </h3>
                    <button
                      onClick={() => loadDatasetPreview(selectedPersona.id)}
                      disabled={loadingDataset}
                      className="text-xs text-purple-400 hover:text-purple-300 transition-colors"
                    >
                      {loadingDataset ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : "Refresh"}
                    </button>
                  </div>

                  {loadingDataset ? (
                    <div className="flex items-center justify-center py-6">
                      <Loader2 className="w-5 h-5 animate-spin text-purple-400" />
                    </div>
                  ) : datasetPreview ? (
                    <div className="space-y-3">
                      <div className="grid grid-cols-4 gap-3">
                        <div className="bg-white/5 rounded-lg p-3 text-center">
                          <p className="text-lg font-bold text-cyan-400">{datasetPreview.total_examples || 0}</p>
                          <p className="text-[10px] text-gray-500">Examples</p>
                        </div>
                        <div className="bg-white/5 rounded-lg p-3 text-center">
                          <p className="text-lg font-bold text-green-400">{datasetPreview.video_count || 0}</p>
                          <p className="text-[10px] text-gray-500">Videos</p>
                        </div>
                        <div className="bg-white/5 rounded-lg p-3 text-center">
                          <p className="text-lg font-bold text-purple-400">{datasetPreview.segment_count || 0}</p>
                          <p className="text-[10px] text-gray-500">Segments</p>
                        </div>
                        <div className="bg-white/5 rounded-lg p-3 text-center">
                          <p className="text-lg font-bold text-yellow-400">{datasetPreview.duration_hours || 0}h</p>
                          <p className="text-[10px] text-gray-500">Duration</p>
                        </div>
                      </div>

                      {/* Sample Examples */}
                      {datasetPreview.preview_examples?.length > 0 && (
                        <div>
                          <p className="text-[10px] text-gray-500 mb-2">Sample Training Data:</p>
                          <div className="space-y-2 max-h-60 overflow-y-auto">
                            {datasetPreview.preview_examples.map((ex, i) => (
                              <div key={i} className="bg-white/5 rounded-lg p-2.5">
                                {ex.messages?.map((msg, j) => (
                                  <p key={j} className={`text-[10px] mb-1 ${
                                    msg.role === "system" ? "text-gray-500" :
                                    msg.role === "user" ? "text-blue-300" : "text-green-300"
                                  }`}>
                                    <span className="font-bold capitalize">{msg.role}: </span>
                                    {msg.content?.substring(0, 200)}{msg.content?.length > 200 ? "..." : ""}
                                  </p>
                                ))}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  ) : (
                    <p className="text-xs text-gray-500 text-center py-4">Tag videos to see dataset preview</p>
                  )}
                </div>

                {/* Training Section */}
                <div className="bg-white/5 rounded-xl border border-white/10 p-5">
                  <h3 className="text-sm font-bold text-gray-200 flex items-center gap-2 mb-3">
                    <Sparkles className="w-4 h-4 text-yellow-400" />
                    Fine-Tuning
                  </h3>

                  {trainingStatus?.logs?.length > 0 && (
                    <div className="space-y-2 mb-4">
                      {trainingStatus.logs.map((log, i) => (
                        <div key={i} className="bg-white/5 rounded-lg p-3">
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              <StatusBadge status={log.status} />
                              <span className="text-xs text-gray-300">{log.base_model}</span>
                            </div>
                            <span className="text-[10px] text-gray-500">
                              {new Date(log.created_at).toLocaleString()}
                            </span>
                          </div>
                          {log.openai_job_id && (
                            <p className="text-[10px] text-gray-500 font-mono mt-1">Job: {log.openai_job_id}</p>
                          )}
                          {log.error_message && (
                            <p className="text-[10px] text-red-400 mt-1">{log.error_message}</p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}

                  <button
                    onClick={handleStartTraining}
                    disabled={
                      taggedVideoIds.size === 0 ||
                      selectedPersona.finetune_status === "training" ||
                      selectedPersona.finetune_status === "preparing"
                    }
                    className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-gradient-to-r from-yellow-500 to-orange-500 hover:from-yellow-600 hover:to-orange-600 text-white text-sm font-bold rounded-lg transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {selectedPersona.finetune_status === "training" ? (
                      <>
                        <Loader2 className="w-4 h-4 animate-spin" />
                        Training in Progress...
                      </>
                    ) : (
                      <>
                        <Zap className="w-4 h-4" />
                        Start Fine-Tuning (gpt-4.1-mini)
                      </>
                    )}
                  </button>

                  {taggedVideoIds.size === 0 && (
                    <p className="text-[10px] text-gray-500 text-center mt-2">
                      Tag at least one video to enable training
                    </p>
                  )}
                </div>

                {/* ── Chat with Persona ── */}
                {selectedPersona.finetune_model_id && (
                  <div className="bg-white/5 rounded-xl border border-white/10">
                    <button
                      onClick={() => { setShowChat(!showChat); setShowScriptGen(false); }}
                      className="w-full flex items-center justify-between p-5 hover:bg-white/5 rounded-xl transition-colors"
                    >
                      <h3 className="text-sm font-bold text-gray-200 flex items-center gap-2">
                        <MessageSquare className="w-4 h-4 text-blue-400" />
                        Chat with {selectedPersona.name}
                      </h3>
                      {showChat ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
                    </button>

                    {showChat && (
                      <div className="border-t border-white/10 p-4 space-y-3">
                        {/* Context input */}
                        <div>
                          <label className="text-[10px] text-gray-500 mb-1 block">Context (optional)</label>
                          <input
                            type="text"
                            value={chatContext}
                            onChange={(e) => setChatContext(e.target.value)}
                            placeholder="e.g., 商品紹介中、シャンプーの説明をしている"
                            className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-xs text-gray-200 placeholder-gray-500"
                          />
                        </div>

                        {/* Chat messages */}
                        <div className="max-h-96 overflow-y-auto space-y-2 min-h-[120px]">
                          {chatMessages.length === 0 && (
                            <p className="text-xs text-gray-500 text-center py-8">
                              {selectedPersona.name}と会話を始めましょう
                            </p>
                          )}
                          {chatMessages.map((msg, i) => (
                            <div
                              key={i}
                              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                            >
                              <div
                                className={`max-w-[80%] rounded-xl px-3.5 py-2.5 text-xs leading-relaxed ${
                                  msg.role === "user"
                                    ? "bg-purple-600/80 text-white"
                                    : msg.error
                                    ? "bg-red-500/20 text-red-300 border border-red-500/30"
                                    : "bg-white/10 text-gray-200"
                                }`}
                              >
                                <p className="whitespace-pre-wrap">{msg.content}</p>
                                {msg.usage && (
                                  <p className="text-[9px] text-gray-500 mt-1">
                                    {msg.usage.total_tokens} tokens
                                  </p>
                                )}
                              </div>
                            </div>
                          ))}
                          {chatLoading && (
                            <div className="flex justify-start">
                              <div className="bg-white/10 rounded-xl px-4 py-3">
                                <Loader2 className="w-4 h-4 animate-spin text-purple-400" />
                              </div>
                            </div>
                          )}
                        </div>

                        {/* Chat input */}
                        <div className="flex gap-2">
                          <input
                            type="text"
                            value={chatInput}
                            onChange={(e) => setChatInput(e.target.value)}
                            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSendChat()}
                            placeholder="メッセージを入力..."
                            className="flex-1 px-3 py-2.5 bg-white/5 border border-white/10 rounded-lg text-sm text-gray-200 placeholder-gray-500"
                            disabled={chatLoading}
                          />
                          <button
                            onClick={handleSendChat}
                            disabled={!chatInput.trim() || chatLoading}
                            className="px-4 py-2.5 bg-purple-600 hover:bg-purple-700 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            <Send className="w-4 h-4" />
                          </button>
                        </div>

                        {chatMessages.length > 0 && (
                          <button
                            onClick={() => setChatMessages([])}
                            className="text-[10px] text-gray-500 hover:text-gray-300 transition-colors"
                          >
                            Clear conversation
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {/* ── Script Generation ── */}
                {selectedPersona.finetune_model_id && (
                  <div className="bg-white/5 rounded-xl border border-white/10">
                    <button
                      onClick={() => { setShowScriptGen(!showScriptGen); setShowChat(false); }}
                      className="w-full flex items-center justify-between p-5 hover:bg-white/5 rounded-xl transition-colors"
                    >
                      <h3 className="text-sm font-bold text-gray-200 flex items-center gap-2">
                        <FileText className="w-4 h-4 text-orange-400" />
                        Script Generator
                      </h3>
                      {showScriptGen ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
                    </button>

                    {showScriptGen && (
                      <div className="border-t border-white/10 p-4 space-y-3">
                        <div>
                          <label className="text-[10px] text-gray-500 mb-1 block">紹介する商品（改行区切り）</label>
                          <textarea
                            value={scriptProducts}
                            onChange={(e) => setScriptProducts(e.target.value)}
                            placeholder="KYOGOKU シャンプー\nKYOGOKU トリートメント"
                            rows={3}
                            className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-xs text-gray-200 placeholder-gray-500 resize-none"
                          />
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <label className="text-[10px] text-gray-500 mb-1 block">配信時間（分）</label>
                            <input
                              type="number"
                              value={scriptDuration}
                              onChange={(e) => setScriptDuration(Number(e.target.value))}
                              min={1}
                              max={60}
                              className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-xs text-gray-200"
                            />
                          </div>
                          <div>
                            <label className="text-[10px] text-gray-500 mb-1 block">スタイル</label>
                            <input
                              type="text"
                              value={scriptStyle}
                              onChange={(e) => setScriptStyle(e.target.value)}
                              placeholder="e.g., ハイテンション"
                              className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-xs text-gray-200 placeholder-gray-500"
                            />
                          </div>
                        </div>

                        <div>
                          <label className="text-[10px] text-gray-500 mb-1 block">備考</label>
                          <input
                            type="text"
                            value={scriptNotes}
                            onChange={(e) => setScriptNotes(e.target.value)}
                            placeholder="e.g., セール中、視聴者参加型"
                            className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-xs text-gray-200 placeholder-gray-500"
                          />
                        </div>

                        <button
                          onClick={handleGenerateScript}
                          disabled={scriptLoading}
                          className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-gradient-to-r from-orange-500 to-red-500 hover:from-orange-600 hover:to-red-600 text-white text-sm font-bold rounded-lg transition-all disabled:opacity-50"
                        >
                          {scriptLoading ? (
                            <><Loader2 className="w-4 h-4 animate-spin" /> 台本生成中...</>
                          ) : (
                            <><FileText className="w-4 h-4" /> 台本を生成</>
                          )}
                        </button>

                        {generatedScript && (
                          <div className="space-y-2">
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-3">
                                <span className="text-[10px] text-gray-500">
                                  {generatedScript.char_count}文字 / 約{generatedScript.estimated_duration_minutes}分
                                </span>
                                <span className="text-[10px] text-gray-500">
                                  {generatedScript.usage?.total_tokens} tokens
                                </span>
                              </div>
                              <button
                                onClick={handleCopyScript}
                                className="flex items-center gap-1 text-[10px] text-purple-400 hover:text-purple-300"
                              >
                                <Copy className="w-3 h-3" /> Copy
                              </button>
                            </div>
                            <div className="bg-white/5 rounded-lg p-4 max-h-96 overflow-y-auto">
                              <pre className="text-xs text-gray-200 whitespace-pre-wrap leading-relaxed font-sans">
                                {generatedScript.script}
                              </pre>
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {/* Model Info */}
                {selectedPersona.finetune_model_id && (
                  <div className="bg-white/5 rounded-xl border border-white/10 p-4">
                    <p className="text-[10px] text-gray-500 mb-1">Fine-tuned Model</p>
                    <p className="text-xs text-gray-300 font-mono break-all">{selectedPersona.finetune_model_id}</p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Create/Edit Modal ── */}
      {showCreateForm && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-900 rounded-2xl border border-white/10 w-full max-w-lg p-6">
            <div className="flex items-center justify-between mb-5">
              <h2 className="text-lg font-bold text-gray-100">
                {editingId ? "Edit Persona" : "Create New Persona"}
              </h2>
              <button
                onClick={() => { setShowCreateForm(false); setEditingId(null); }}
                className="p-1.5 hover:bg-white/10 rounded-lg"
              >
                <X className="w-5 h-5 text-gray-400" />
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="text-xs text-gray-400 mb-1 block">Name *</label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  placeholder="e.g., Ryukyogoku"
                  className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-sm text-gray-200 placeholder-gray-500"
                />
              </div>

              <div>
                <label className="text-xs text-gray-400 mb-1 block">Description</label>
                <input
                  type="text"
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                  placeholder="e.g., KYOGOKU hair care expert"
                  className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-sm text-gray-200 placeholder-gray-500"
                />
              </div>

              <div>
                <label className="text-xs text-gray-400 mb-1 block">Speaking Style</label>
                <textarea
                  value={formData.speaking_style}
                  onChange={(e) => setFormData({ ...formData, speaking_style: e.target.value })}
                  placeholder="e.g., Energetic and friendly, uses casual Japanese"
                  rows={3}
                  className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-sm text-gray-200 placeholder-gray-500 resize-none"
                />
              </div>

              <div>
                <label className="text-xs text-gray-400 mb-1 block">Catchphrases (comma-separated)</label>
                <input
                  type="text"
                  value={formData.catchphrases}
                  onChange={(e) => setFormData({ ...formData, catchphrases: e.target.value })}
                  placeholder="e.g., すごいでしょ, これマジでいいよ"
                  className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-sm text-gray-200 placeholder-gray-500"
                />
              </div>

              <div>
                <label className="text-xs text-gray-400 mb-1 block">Personality Traits (comma-separated)</label>
                <input
                  type="text"
                  value={formData.personality_traits}
                  onChange={(e) => setFormData({ ...formData, personality_traits: e.target.value })}
                  placeholder="e.g., energetic, knowledgeable, friendly"
                  className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-sm text-gray-200 placeholder-gray-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-gray-400 mb-1 block">Voice ID (ElevenLabs)</label>
                  <input
                    type="text"
                    value={formData.voice_id}
                    onChange={(e) => setFormData({ ...formData, voice_id: e.target.value })}
                    placeholder="Optional"
                    className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-sm text-gray-200 placeholder-gray-500"
                  />
                </div>
                <div>
                  <label className="text-xs text-gray-400 mb-1 block">Language</label>
                  <select
                    value={formData.language}
                    onChange={(e) => setFormData({ ...formData, language: e.target.value })}
                    className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-sm text-gray-200"
                  >
                    <option value="ja">Japanese</option>
                    <option value="zh">Chinese</option>
                    <option value="en">English</option>
                    <option value="ko">Korean</option>
                    <option value="vi">Vietnamese</option>
                  </select>
                </div>
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => { setShowCreateForm(false); setEditingId(null); }}
                className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSavePersona}
                disabled={!formData.name.trim() || saving}
                className="flex items-center gap-2 px-5 py-2 bg-purple-600 hover:bg-purple-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50"
              >
                {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                {editingId ? "Update" : "Create"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
