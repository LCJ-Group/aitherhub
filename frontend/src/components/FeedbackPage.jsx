import { useState } from "react";
import { toast } from "../hooks/use-toast";
import FeedbackService from "../base/services/feedbackService";
import { useTranslation } from 'react-i18next';

export default function FeedbackPage({ onBack }) {
    useTranslation(); // triggers re-render on language change
    const [feedbackText, setFeedbackText] = useState("");
    const [isSubmitting, setIsSubmitting] = useState(false);

    const handleSubmit = async () => {
        if (!feedbackText.trim()) {
            toast.error(window.__t('feedbackEmptyError') || 'Please enter your feedback');
            return;
        }

        setIsSubmitting(true);
        try {
            await FeedbackService.submit(feedbackText);
            toast.success(window.__t('feedbackSuccess') || 'Feedback sent successfully!');
            setFeedbackText("");
            onBack?.();
        } catch (error) {
            const errorMessage = error?.response?.data?.detail || error?.message || window.__t('feedbackError') || 'Failed to send feedback';
            toast.error(errorMessage);
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <div className="flex-1 flex items-center justify-center p-0 md:p-4">
            <div className="w-full max-w-lg">
                <div className="bg-white/10 backdrop-blur-sm border border-white/30 rounded-2xl p-4 md:p-8">
                    <div className="flex flex-col items-center text-center space-y-6">
                        {/* Icon */}
                        <div className="w-16 h-16 rounded-full bg-white/20 flex items-center justify-center">
                            <svg
                                xmlns="http://www.w3.org/2000/svg"
                                width="28"
                                height="28"
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="white"
                                strokeWidth="2"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                            >
                                <path d="M22 17a2 2 0 0 1-2 2H6.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 2 21.286V5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2z" />
                            </svg>
                        </div>

                        {/* Title */}
                        <div>
                            <h2 className="text-2xl font-bold text-white mb-2">
                                {window.__t('feedbackTitle') || 'フィードバックを送信'}
                            </h2>
                            <p className="text-white/70">
                                {window.__t('feedbackDescription') || 'ご意見・ご要望をお聞かせください'}
                            </p>
                        </div>

                        {/* Textarea */}
                        <textarea
                            value={feedbackText}
                            onChange={(e) => setFeedbackText(e.target.value)}
                            placeholder={window.__t('feedbackPlaceholder') || 'フィードバックを入力...'}
                            className="flex w-full rounded-md border px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 min-h-[160px] bg-white/10 border-white/30 text-white placeholder:text-white/50 focus-visible:ring-white/50 resize-none"
                        />

                        {/* Buttons */}
                        <div className="flex gap-4 w-full">
                            <button
                                onClick={onBack}
                                className="flex-1 h-11 rounded-lg text-white/80 border hover:text-white hover:bg-white/10 cursor-pointer transition-all duration-200 active:scale-[0.99] active:bg-white/10"
                            >
                                {window.__t('feedbackBack') || '戻る'}
                            </button>
                            <button
                                onClick={handleSubmit}
                                disabled={isSubmitting}
                                className="flex-1 h-11 rounded-lg bg-white text-[#7D01FF] font-extralight hover:bg-white/90 cursor-pointer transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed active:scale-[0.99] active:bg-white/90"
                            >
                                {isSubmitting
                                    ? (window.__t('processing') || '処理中...')
                                    : (window.__t('feedbackSubmit') || '送信する')
                                }
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}

