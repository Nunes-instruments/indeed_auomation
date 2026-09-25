"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Activity, Bell, BriefcaseBusiness, CalendarDays, CheckCircle2, ChevronRight, Clock3, ExternalLink, Home as HomeIcon, Mail, MailCheck, MessageCircle, Radio, Search, Send, Settings as SettingsIcon, Settings2, ShieldCheck, Sparkles, Star, Users, LoaderCircle, Save, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { RoleReview } from "@/components/role-review";


type ViewKey =
  | "overview"
  | "candidates"
  | "sent-responses"
  | "role-review"
  | "message"
  | "settings"
  | "activity";

const VIEW_KEYS: ViewKey[] = [
  "overview",
  "candidates",
  "sent-responses",
  "role-review",
  "message",
  "settings",
  "activity",
];

const VIEW_META: Record<ViewKey, { title: string; subtitle: string }> = {
  overview: {
    title: "Candidate Management",
    subtitle: "Live applicant monitoring, communication status and review queue.",
  },
  candidates: {
    title: "Candidates",
    subtitle: "Live applicant queue with verified contact and role status.",
  },
  "sent-responses": {
    title: "Sent Responses",
    subtitle: "Permanent acknowledgement and correction history.",
  },
  "role-review": {
    title: "Automated Role Ranking",
    subtitle: "Automatic role-based shortlist and ranking from Indeed job requirements and resume evidence.",
  },
  message: {
    title: "Recruitment Messages",
    subtitle: "Edit acknowledgement and interview email/WhatsApp templates.",
  },
  settings: {
    title: "Settings",
    subtitle: "Company, sender authentication and local automation configuration.",
  },
  activity: {
    title: "Activity",
    subtitle: "Recent monitoring, extraction and delivery events.",
  },
};

type Applicant = {
  id: number;
  candidate_name?: string | null;
  candidate_email?: string | null;
  candidate_phone?: string | null;
  job_title?: string | null;
  indeed_status?: string | null;
  profile_url?: string | null;
  resume_path?: string | null;
  extraction_status?: string | null;
  application_verified?: number | boolean | null;
  application_checked_at?: string | null;
  decision_reason?: string | null;
  send_status?: string | null;
  send_error?: string | null;
  sent_at?: string | null;
  first_seen_at?: string | null;
  last_seen_at?: string | null;
};

type LogRow = {
  id: number;
  level: string;
  message: string;
  created_at: string;
};

type SentResponse = {
  id: number;
  recipient_email: string;
  candidate_name?: string | null;
  candidate_phone?: string | null;
  job_title?: string | null;
  subject?: string | null;
  body?: string | null;
  source_key?: string | null;
  smtp_transport?: string | null;
  message_id?: string | null;
  sent_at: string;
  history_source: string;
  correction_required?: number;
  correction_status?: string | null;
  correction_subject?: string | null;
  correction_body?: string | null;
  correction_sent_at?: string | null;
  correction_error?: string | null;
};


type Settings = {
  company_name: string;
  company_email: string;
  smtp_app_password_set: boolean;
  indeed_start_url: string;
  indeed_candidates_url: string;
  automation_enabled: boolean;
  monitoring_enabled: boolean;
  auto_scan: boolean;
  auto_send: boolean;
  allow_candidate_page_email_fallback: boolean;
  local_ai_enabled: boolean;
  ollama_url: string;
  ollama_model: string;
  openai_ranking_enabled: boolean;
  openai_ranking_model: string;
  openai_ranking_min_local_score: number;
  openai_ranking_max_local_score: number;
  openai_ranking_ai_weight: number;
  openai_ranking_daily_call_limit: number;
  openai_ranking_key_configured: boolean;
  openai_ranking_key_storage?: string | null;
  subject_template: string;
  body_template: string;
  whatsapp_ack_template: string;
  interview_subject_template: string;
  interview_body_template: string;
  whatsapp_interview_template: string;
  hr_report_sender_email: string;
  hr_report_recipient: string;
  hr_report_smtp_app_password_set: boolean;
  auto_send_role_reports: boolean;
  daily_consolidated_report_enabled: boolean;
  daily_report_time: string;
  whatsapp_enabled: boolean;
  whatsapp_auto_connect: boolean;
  whatsapp_default_country_code: string;
  persistence?: {
    saved_at?: string | null;
    policy?: string;
    scope?: string;
    candidate_gmail_saved?: boolean;
    hr_gmail_saved?: boolean;
    openai_saved?: boolean;
    indeed_reuses_chrome?: boolean;
    whatsapp_reuses_chrome?: boolean;
    github_repository?: string;
    github_branch?: string;
    message?: string;
  };
};

type Dashboard = {
  version: string;
  settings: Settings;
  stats: {
    total: number;
    ready: number;
    sent: number;
    review: number;
    skipped: number;
    application_verified: number;
    last_scan_at?: string | null;
    last_new_count?: string | null;
    last_visible_count?: string | null;
    backlog_completed: boolean;
    backlog_completed_at?: string | null;
  };
  chrome: {
    ok: boolean;
    chrome_connected?: boolean;
    indeed_found?: boolean;
    message?: string;
    url?: string;
    title?: string;
    devtools_port?: number;
    error?: string;
  };
  detected_candidates_page?: {
    url: string;
    title: string;
  } | null;
  detected_candidates_error?: string | null;
  email_configured: boolean;
  email_verified?: boolean;
  email_last_error?: string | null;
  email_transport?: string | null;
  automation_enabled: boolean;
  live_detection: {
    healthy: boolean;
    status: "LIVE" | "STALE" | "ERROR" | "STARTING" | "OFF" | string;
    started_at?: string | null;
    heartbeat_at?: string | null;
    last_success_at?: string | null;
    seconds_since_success?: number | null;
    seconds_since_heartbeat?: number | null;
    scan_mode?: string | null;
    last_error?: string | null;
    consecutive_failures: number;
    last_found_links: number;
    new_last_scan: number;
    detected_today: number;
    verified_today: number;
    review_today: number;
    sent_today: number;
    skipped_today: number;
    seen_today: number;
    last_seen_today_applicant?: {
      candidate_name?: string | null;
      candidate_email?: string | null;
      job_title?: string | null;
      first_detected_at?: string | null;
      last_seen_at?: string | null;
      send_status?: string | null;
      extraction_status?: string | null;
    } | null;
    local_date?: string | null;
    last_new_applicant?: {
      candidate_name?: string | null;
      candidate_email?: string | null;
      job_title?: string | null;
      detected_at?: string | null;
      send_status?: string | null;
      extraction_status?: string | null;
    } | null;
  };
  auto_connection?: {
    phase?: string;
    message?: string;
    last_error?: string | null;
    retry_in_seconds?: number;
  };
  applicants: Applicant[];
  sent_responses: SentResponse[];
  logs: LogRow[];
  role_review_roles?: Array<{
    job_title: string;
    applicant_count: number;
    analyzed_count: number;
    waiting_count: number;
    auto_shortlisted_count: number;
    description_status?: string | null;
    description_source?: string | null;
  }>;
  role_ranking_status?: {
    total_roles: number;
    roles_ready: number;
    roles_waiting_description: number;
    ranked_candidates: number;
    waiting_candidates: number;
    auto_shortlisted: number;
    removed_candidates?: number;
    shortlist_threshold: number;
  };
  role_ranking_error?: string | null;
  recruitment_pipeline?: {
    roles?: { total?: number; open?: number; paused?: number; closed?: number };
    notifications?: { queued?: number; sent?: number; failed?: number; waiting_login?: number };
    hr_approved?: number;
    report_sender?: string;
    report_recipient?: string;
    report_gmail_configured?: boolean;
    report_gmail_verified?: boolean;
    report_gmail_error?: string | null;
    daily_report?: {
      enabled?: boolean;
      time?: string;
      sender?: string;
      recipient?: string;
      last_queued_date?: string | null;
      last_queued_at?: string | null;
      last_sent_date?: string | null;
      last_sent_at?: string | null;
      last_error?: string | null;
    };
    whatsapp_enabled?: boolean;
    whatsapp_auto_connect?: boolean;
    whatsapp_status?: string;
    whatsapp_last_sent_at?: string | null;
    interview_schedule?: { interview_date?: string | null; rule?: string | null };
  };
  backend_health?: {
    schema?: { ready?: boolean; error?: string | null };
    database?: {
      ok?: boolean;
      path?: string;
      size_bytes?: number;
      applications?: number;
      sent_responses?: number;
      logs?: number;
      error?: string | null;
    };
    settings_saved_at?: string | null;
    errors?: string[];
  };
};


type OverviewRole = {
  job_title: string;
  applicant_count: number;
  active_applicant_count?: number;
  analyzed_count: number;
  waiting_count: number;
  auto_shortlisted_count: number;
  removed_count?: number;
  acknowledged_count?: number;
  ranked_count?: number;
  hr_review_count?: number;
  interview_count?: number;
  completed_count?: number;
  top_match_count?: number;
  lifecycle_status?: string | null;
  report_status?: string | null;
  description_status?: string | null;
};

type OverviewReviewApplicant = Applicant & {
  analysis_status?: string | null;
  match_score?: number | null;
  rank_position?: number | null;
  auto_shortlisted?: number | boolean | null;
  hr_flow?: {
    hr_status?: string | null;
    interview_date?: string | null;
  } | null;
};

type OverviewRolePayload = {
  role?: OverviewRole & {
    lifecycle?: {
      lifecycle_status?: string | null;
      report_status?: string | null;
    };
  };
  applicants?: OverviewReviewApplicant[];
  active_applicants?: OverviewReviewApplicant[];
  removed?: OverviewReviewApplicant[];
  ranking?: {
    ranked?: number;
    waiting?: number;
    auto_shortlisted?: number;
    removed?: number;
    threshold?: number;
  };
};

const DEFAULT_SETTINGS: Settings = {
  company_name: "Nunes",
  company_email: "nuneslead@gmail.com",
  smtp_app_password_set: false,
  indeed_start_url: "",
  indeed_candidates_url: "",
  automation_enabled: true,
  monitoring_enabled: true,
  auto_scan: true,
  auto_send: true,
  allow_candidate_page_email_fallback: true,
  local_ai_enabled: false,
  ollama_url: "",
  ollama_model: "",
  openai_ranking_enabled: true,
  openai_ranking_model: "gpt-5.6-luna",
  openai_ranking_min_local_score: 45,
  openai_ranking_max_local_score: 90,
  openai_ranking_ai_weight: 0.12,
  openai_ranking_daily_call_limit: 40,
  openai_ranking_key_configured: false,
  openai_ranking_key_storage: null,
  subject_template: "Thank you for applying – {job_title}",
  body_template: "Dear {candidate_name},\n\nThank you for applying for the {job_title} position at {company_name}.\n\nWe have received your application and our team will review your profile. If your experience matches the role, we will contact you regarding the next steps.\n\nRegards,\n{company_name}",
  whatsapp_ack_template: "Dear {candidate_name}, thank you for applying for the {job_title} position at {company_name}. We have received your application. Our recruitment team will review your profile and contact you regarding the next steps.",
  interview_subject_template: "Interview invitation – {job_title} – {interview_date}",
  interview_body_template: "Dear {candidate_name},\n\nOur HR team has reviewed your application for the {job_title} position at {company_name} and selected you for the interview stage.\n\nYour interview is scheduled for {interview_date}. Our HR team will contact you with the time and venue/meeting details.\n\nRegards,\n{company_name}",
  whatsapp_interview_template: "Dear {candidate_name}, our HR team has selected your application for the {job_title} position at {company_name}. Your interview is scheduled for {interview_date}. Our HR team will contact you with the time and venue/meeting details.",
  hr_report_sender_email: "nunescbe@gmail.com",
  hr_report_recipient: "nunescbe@gmail.com",
  hr_report_smtp_app_password_set: false,
  auto_send_role_reports: false,
  daily_consolidated_report_enabled: true,
  daily_report_time: "19:00",
  whatsapp_enabled: true,
  whatsapp_auto_connect: true,
  whatsapp_default_country_code: "91",
};

const DEFAULT_DASHBOARD: Dashboard = {
  version: "V11.11.4",
  settings: DEFAULT_SETTINGS,
  stats: {
    total: 0,
    ready: 0,
    sent: 0,
    review: 0,
    skipped: 0,
    application_verified: 0,
    backlog_completed: false,
  },
  chrome: { ok: false, message: "Starting" },
  detected_candidates_page: null,
  detected_candidates_error: null,
  email_configured: false,
  email_verified: false,
  automation_enabled: true,
  live_detection: {
    healthy: false,
    status: "STARTING",
    consecutive_failures: 0,
    last_found_links: 0,
    new_last_scan: 0,
    detected_today: 0,
    verified_today: 0,
    review_today: 0,
    sent_today: 0,
    skipped_today: 0,
    seen_today: 0,
    last_seen_today_applicant: null,
  },
  auto_connection: { phase: "starting", message: "Starting" },
  applicants: [],
  sent_responses: [],
  logs: [],
  role_review_roles: [],
  role_ranking_status: {
    total_roles: 0,
    roles_ready: 0,
    roles_waiting_description: 0,
    ranked_candidates: 0,
    waiting_candidates: 0,
    auto_shortlisted: 0,
    shortlist_threshold: 70,
  },
  role_ranking_error: null,
  recruitment_pipeline: {
    roles: { total: 0, open: 0, paused: 0, closed: 0 },
    notifications: { queued: 0, sent: 0, failed: 0, waiting_login: 0 },
    hr_approved: 0,
    report_sender: "nunescbe@gmail.com",
    report_recipient: "nunescbe@gmail.com",
    report_gmail_configured: false,
    report_gmail_verified: false,
    whatsapp_enabled: true,
    whatsapp_status: "NOT_CONNECTED",
  },
};

const api = async (path: string, options?: RequestInit) => {
  const response = await fetch(`/backend${path}`, {
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers || {}),
    },
    ...options,
  });

  const raw = await response.text();
  let data: any = {};

  if (raw) {
    try {
      data = JSON.parse(raw);
    } catch {
      const compact = raw.replace(/\s+/g, " ").trim();
      throw new Error(
        compact
          ? `Backend returned HTTP ${response.status}: ${compact.slice(0, 220)}`
          : `Backend returned HTTP ${response.status}`,
      );
    }
  }

  if (!response.ok) {
    const backendMessage = String(data?.message || "").trim();
    const backendDetail = String(data?.detail || "").trim();
    throw new Error(
      backendMessage === "Internal backend error" && backendDetail
        ? `Backend: ${backendDetail}`
        : backendMessage || backendDetail || `Request failed (${response.status})`,
    );
  }

  return data;
};

const formatDate = (value?: string | null) => {
  if (!value) return "—";
  try {
    return new Intl.DateTimeFormat("en-IN", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(value));
  } catch {
    return value;
  }
};

const liveAge = (seconds?: number | null) => {
  if (seconds === null || seconds === undefined) return "Waiting for first check";
  if (seconds <= 1) return "Checked just now";
  if (seconds < 60) return `Checked ${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  return `Checked ${minutes}m ago`;
};

const relativeTime = (value?: string | null) => {
  if (!value) return "Waiting";
  const when = new Date(value).getTime();
  if (!Number.isFinite(when)) return "Waiting";
  const seconds = Math.max(0, Math.floor((Date.now() - when) / 1000));
  if (seconds < 10) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
};


const isLocalToday = (value?: string | null) => {
  if (!value) return false;

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return false;

  const now = new Date();

  return (
    date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate()
  );
};


const mailErrorSummary = (value?: string | null) => {
  const text = (value || "").trim();
  if (!text) return "";

  const lower = text.toLowerCase();

  if (
    lower.includes("app password")
    || lower.includes("authentication")
    || lower.includes("535")
    || lower.includes("534")
  ) {
    return "Gmail App Password rejected";
  }

  if (
    lower.includes("network")
    || lower.includes("connect")
    || lower.includes("timed out")
    || lower.includes("timeout")
  ) {
    return "Gmail connection failed";
  }

  if (lower.includes("recipient")) {
    return "Recipient rejected";
  }

  return text.length > 72 ? `${text.slice(0, 69)}…` : text;
};


const reviewLabel = (value?: string | null) => {
  const status = (value || "").toUpperCase();

  const labels: Record<string, string> = {
    VERIFIED_EMAIL_AND_ROLE: "Verified email + role",
    VERIFIED_EMAIL_FOUND: "Verified",
    NEEDS_REVIEW_APPLICATION: "Verifying application",
    SKIPPED_NO_EMAIL_IN_RESUME: "Skipped · no email",
    SKIPPED_AMBIGUOUS_EMAIL: "Skipped · ambiguous email",
    NEEDS_REVIEW_ROLE: "Waiting for role",
    NEEDS_REVIEW_JOB_TITLE: "Waiting for role",
    NEEDS_REVIEW_NO_VERIFIED_EMAIL: "Waiting for verified email",
    NEEDS_REVIEW_NO_EMAIL_IN_RESUME: "Waiting for verified email",
    NEEDS_REVIEW_AMBIGUOUS_EMAIL: "Multiple emails — review",
    NEEDS_REVIEW_NO_RESUME_FOUND: "Resume not available yet",
    NEEDS_REVIEW_RESUME_NO_TEXT: "Resume text unavailable",
  };

  return labels[status] || (value || "Pending").replaceAll("_", " ");
};


const statusTone = (value?: string | null) => {
  const v = (value || "").toUpperCase();
  if (v.includes("SENT") || v.includes("VERIFIED")) return "success";
  if (v.includes("READY")) return "info";
  if (v.includes("REVIEW") || v.includes("FAILED")) return "warning";
  return "neutral";
};

export default function Home() {
  const [data, setData] = useState<Dashboard>(DEFAULT_DASHBOARD);
  const [busy, setBusy] = useState<string>("");
  const [notice, setNotice] = useState<{tone: "ok" | "warn" | "error"; text: string} | null>(null);
  const [search, setSearch] = useState("");
  const [candidateScope, setCandidateScope] = useState<"today" | "history">("today");
  const [sentSearch, setSentSearch] = useState("");
  const [activeView, setActiveView] = useState<ViewKey>("overview");
  const [password, setPassword] = useState("");
  const [hrReportPassword, setHrReportPassword] = useState("");
  const [openaiRankingKey, setOpenaiRankingKey] = useState("");
  const [settingsDraft, setSettingsDraft] = useState<Settings>(DEFAULT_SETTINGS);
  const [viewError, setViewError] = useState("");
  const [headerSearch, setHeaderSearch] = useState("");
  const [overviewRoles, setOverviewRoles] = useState<OverviewRole[]>([]);
  const [overviewRoleDetails, setOverviewRoleDetails] = useState<Record<string, OverviewRolePayload>>({});
  const [overviewApplicants, setOverviewApplicants] = useState<Applicant[]>([]);
  const [clockNow, setClockNow] = useState(() => new Date());
  const settingsDirtyRef = useRef(false);

  const load = useCallback(async () => {
    try {
      const next = await api("/api/dashboard?lite=1");
      setData((current) => ({
        ...current,
        ...next,
        applicants: current.applicants,
        sent_responses: current.sent_responses,
        logs: current.logs,
      }));
      if (next.settings && !settingsDirtyRef.current) {
        setSettingsDraft(next.settings);
      }
      setNotice((current) =>
        current?.tone === "error" && current.text.includes("Unable to load")
          ? null
          : current
      );

      try {
        localStorage.setItem(
          "nunes-recruitment-dashboard-cache",
          JSON.stringify({
            version: next.version,
            settings: next.settings,
            stats: next.stats,
            chrome: next.chrome,
            detected_candidates_page: next.detected_candidates_page,
            email_configured: next.email_configured,
            email_verified: next.email_verified,
            automation_enabled: next.automation_enabled,
            live_detection: next.live_detection,
            auto_connection: next.auto_connection,
            role_ranking_status: next.role_ranking_status,
          })
        );
      } catch {}
    } catch (error) {
      setNotice({
        tone: "warn",
        text: error instanceof Error
          ? `Backend starting: ${error.message}`
          : "Backend is starting",
      });
    }
  }, []);

  const loadViewData = useCallback(async (view: ViewKey) => {
    try {
      if (view === "candidates") {
        const result = await api("/api/applicants?limit=500");
        setData((current) => ({
          ...current,
          applicants: result.applicants || [],
        }));
      } else if (view === "sent-responses") {
        const result = await api("/api/sent-responses?limit=1000");
        setData((current) => ({
          ...current,
          sent_responses: result.sent_responses || [],
        }));
      } else if (view === "activity") {
        const result = await api("/api/activity?limit=100");
        setData((current) => ({
          ...current,
          logs: result.logs || [],
        }));
      }
      setViewError("");
    } catch (error) {
      setViewError(
        error instanceof Error
          ? error.message
          : "Unable to load this saved view",
      );
    }
  }, []);

  const loadRoleStatus = useCallback(async () => {
    try {
      const result = await api("/api/role-ranking-status");
      setData((current) => ({
        ...current,
        role_ranking_status: result.role_ranking_status || current.role_ranking_status,
      }));
    } catch {}
  }, []);


  const loadOverviewExtras = useCallback(async () => {
    try {
      const result = await api("/api/recruitment/overview?roles=6&recent=8");
      setOverviewRoles((result.roles || []) as OverviewRole[]);
      setOverviewApplicants((result.recent || []) as Applicant[]);
      setOverviewRoleDetails({});
    } catch {
      // The lightweight dashboard continues rendering while this optional
      // operations summary retries on the next interval.
    }
  }, []);

  const loadCurrentSettings = useCallback(async () => {
    try {
      const result = await api("/api/settings");
      if (result.settings && !settingsDirtyRef.current) {
        setSettingsDraft(result.settings);
      }
      setData((current) => ({
        ...current,
        settings: result.settings || current.settings,
        email_configured: Boolean(result.email_configured),
        email_verified: Boolean(result.email_verified),
        email_last_error: result.email_last_error ?? current.email_last_error,
        email_transport: result.email_transport ?? current.email_transport,
      }));
      setViewError("");
    } catch (error) {
      setViewError(
        error instanceof Error ? error.message : "Unable to load saved settings",
      );
    }
  }, []);

  const updateSetting = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    settingsDirtyRef.current = true;
    setSettingsDraft((current) => ({ ...current, [key]: value }));
  };

  useEffect(() => {
    try {
      const raw = localStorage.getItem("nunes-recruitment-dashboard-cache");
      if (raw) {
        const cached = JSON.parse(raw);
        setData((current) => ({ ...current, ...cached }));
        if (cached.settings) {
          setSettingsDraft(cached.settings);
        }
      }
    } catch {}

    load();
    const timer = window.setInterval(load, 2000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    const first = window.setTimeout(loadRoleStatus, 800);
    const timer = window.setInterval(loadRoleStatus, 10000);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(timer);
    };
  }, [loadRoleStatus]);


  useEffect(() => {
    if (activeView !== "overview") return;
    const first = window.setTimeout(loadOverviewExtras, 450);
    const timer = window.setInterval(loadOverviewExtras, 15000);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(timer);
    };
  }, [activeView, loadOverviewExtras]);

  useEffect(() => {
    const timer = window.setInterval(() => setClockNow(new Date()), 30000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    loadViewData(activeView);

    if (!["candidates", "sent-responses", "activity"].includes(activeView)) {
      return;
    }

    const interval = activeView === "candidates" ? 2000 : 5000;
    const timer = window.setInterval(
      () => loadViewData(activeView),
      interval,
    );
    return () => window.clearInterval(timer);
  }, [activeView, loadViewData]);

  useEffect(() => {
    if (activeView === "settings" || activeView === "message") {
      loadCurrentSettings();
    }
  }, [activeView, loadCurrentSettings]);

  useEffect(() => {
    const syncViewFromLocation = () => {
      const hash = window.location.hash.replace("#", "") as ViewKey;
      if (VIEW_KEYS.includes(hash)) {
        setActiveView(hash);
      } else {
        setActiveView("overview");
      }
    };

    syncViewFromLocation();
    window.addEventListener("hashchange", syncViewFromLocation);
    window.addEventListener("popstate", syncViewFromLocation);

    return () => {
      window.removeEventListener("hashchange", syncViewFromLocation);
      window.removeEventListener("popstate", syncViewFromLocation);
    };
  }, []);

  const navigateTo = (view: ViewKey) => {
    setActiveView(view);
    window.history.pushState(null, "", `#${view}`);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  useEffect(() => {
    if (!settingsDirtyRef.current && data?.settings) {
      setSettingsDraft(data.settings);
    }
  }, [data?.settings]);

  const runAction = async (
    key: string,
    path: string,
    successFallback: string,
    body?: unknown,
  ) => {
    setBusy(key);
    setNotice(null);
    try {
      const result = await api(path, {
        method: "POST",
        body: JSON.stringify(body ?? {}),
      });
      setNotice({
        tone: "ok",
        text: result.message || successFallback,
      });
      await load();
    } catch (error) {
      setNotice({
        tone: "error",
        text: error instanceof Error ? error.message : "Action failed",
      });
    } finally {
      setBusy("");
    }
  };

  const saveSettings = async () => {
    if (!settingsDraft) return;
    setBusy("settings");
    setNotice(null);

    try {
      const payload: Record<string, unknown> = {
        ...settingsDraft,
      };

      if (password.trim()) {
        payload.smtp_app_password = password.trim();
      }
      if (hrReportPassword.trim()) {
        payload.hr_report_smtp_app_password = hrReportPassword.trim();
      }

      const result = await api("/api/settings", {
        method: "POST",
        body: JSON.stringify(payload),
      });

      setPassword("");
      setHrReportPassword("");
      settingsDirtyRef.current = false;
      setSettingsDraft(result.settings);
      setData((current) => ({
        ...current,
        settings: result.settings || current.settings,
        email_configured: Boolean(result.email_configured),
        email_verified: Boolean(result.email_verified),
      }));
      setNotice({
        tone: "ok",
        text: result.message || "Settings saved.",
      });
      setViewError("");
      await load();
    } catch (error) {
      setNotice({
        tone: "error",
        text: error instanceof Error ? error.message : "Unable to save settings",
      });
    } finally {
      setBusy("");
    }
  };

  const saveOpenAIRankingKey = async () => {
    const key = openaiRankingKey.trim();
    if (!key) {
      setNotice({ tone: "warn", text: "Paste the new OpenAI ranking API key first." });
      return;
    }
    setBusy("openai-key");
    setNotice(null);
    try {
      const result = await api("/api/ranking/openai/key", {
        method: "POST",
        body: JSON.stringify({ api_key: key }),
      });
      setOpenaiRankingKey("");
      settingsDirtyRef.current = false;
      setNotice({ tone: "ok", text: result.message || "OpenAI ranking key saved securely." });
      await load();
    } catch (error) {
      setNotice({
        tone: "error",
        text: error instanceof Error ? error.message : "Unable to save ranking API key",
      });
    } finally {
      setBusy("");
    }
  };

  const filteredApplicants = useMemo(() => {
    const rows = data?.applicants || [];
    const q = search.trim().toLowerCase();

    return rows.filter((row) => {
      if (
        candidateScope === "today"
        && !isLocalToday(row.last_seen_at)
      ) {
        return false;
      }

      if (!q) return true;

      return [
        row.candidate_name,
        row.candidate_email,
        row.candidate_phone,
        row.job_title,
        row.extraction_status,
        row.send_status,
        row.decision_reason,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(q);
    });
  }, [data?.applicants, search, candidateScope]);

  const filteredSentResponses = useMemo(() => {
    const rows = data?.sent_responses || [];
    const q = sentSearch.trim().toLowerCase();

    if (!q) return rows;

    return rows.filter((row) =>
      [
        row.candidate_name,
        row.recipient_email,
        row.candidate_phone,
        row.job_title,
        row.subject,
        row.body,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(q)
    );
  }, [data?.sent_responses, sentSearch]);

  const sentResponseSummary = useMemo(() => {
    const rows = data?.sent_responses || [];
    const uniqueRecipients = new Set(
      rows
        .map((row) => (row.recipient_email || "").trim().toLowerCase())
        .filter(Boolean)
    ).size;
    const corrections = rows.filter(
      (row) => row.correction_status === "SENT" || Boolean(row.correction_sent_at)
    ).length;
    const latest = rows.reduce<string | null>((current, row) => {
      if (!row.sent_at) return current;
      if (!current) return row.sent_at;
      return new Date(row.sent_at).getTime() > new Date(current).getTime()
        ? row.sent_at
        : current;
    }, null);

    return {
      total: rows.length,
      uniqueRecipients,
      corrections,
      latest,
    };
  }, [data?.sent_responses]);

  const chromeConnected = Boolean(data.chrome?.ok);
  const candidatesReady = Boolean(data.detected_candidates_page);
  const monitoringActive =
    settingsDraft.automation_enabled &&
    settingsDraft.monitoring_enabled &&
    settingsDraft.auto_scan &&
    Boolean(settingsDraft.indeed_candidates_url);

  const rolePipelineRows = useMemo(() => {
    return overviewRoles.slice(0, 6).map((role) => {
      const active = Number(role.active_applicant_count ?? role.applicant_count ?? 0);
      return {
        ...role,
        newCount: active,
        acknowledged: Number(role.acknowledged_count || 0),
        ranked: Number(role.ranked_count ?? role.analyzed_count ?? 0),
        hrReview: Number(role.hr_review_count || 0),
        interview: Number(role.interview_count || 0),
        completed: Number(role.completed_count ?? role.removed_count ?? 0),
      };
    });
  }, [overviewRoles]);

  const roleHrReviewTotal = useMemo(
    () => rolePipelineRows.reduce((sum, role) => sum + role.hrReview, 0),
    [rolePipelineRows],
  );

  const completedCandidates = Number(
    data.role_ranking_status?.removed_candidates
      ?? rolePipelineRows.reduce((sum, role) => sum + role.completed, 0),
  );
  const interviewCandidates = Number(data.recruitment_pipeline?.hr_approved || 0);
  const rankedCandidates = Number(data.role_ranking_status?.ranked_candidates || 0);
  const rankingTotal = Math.max(
    0,
    rankedCandidates + Number(data.role_ranking_status?.waiting_candidates || 0),
  );
  const rankingPercent = rankingTotal > 0
    ? Math.round((rankedCandidates / rankingTotal) * 100)
    : 0;
  const pipelineBase = Math.max(Number(data.stats.total || 0), 1);
  const stagePercent = (value: number) => Math.max(0, Math.min(100, Math.round((value / pipelineBase) * 100)));
  const roleStagePercent = (value: number, total: number) => {
    const base = Math.max(Number(total || 0), 1);
    return Math.max(0, Math.min(100, Math.round((Number(value || 0) / base) * 100)));
  };

  const recentProcessing = useMemo(() => {
    return overviewApplicants
      .map((row) => row as OverviewReviewApplicant)
      .sort((a, b) => {
        const at = new Date(a.first_seen_at || 0).getTime();
        const bt = new Date(b.first_seen_at || 0).getTime();
        return bt - at;
      })
      .slice(0, 5);
  }, [overviewApplicants]);

  const openRoleReview = (jobTitle?: string) => {
    if (jobTitle) {
      try {
        sessionStorage.setItem("nunes-selected-role", jobTitle);
      } catch {}
    }
    navigateTo("role-review");
  };

  const submitHeaderSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const query = headerSearch.trim();
    if (!query) return;
    setSearch(query);
    setCandidateScope("history");
    navigateTo("candidates");
  };

  return (
    <main className="appShell topNavShell">
      <header className="globalTopNav">
        <div className="topBrandGroup">
          <Button type="button" variant="ghost" className="topBrand" onClick={() => navigateTo("overview")}>
            <span className="topBrandMark">N</span>
            <span>
              <strong>Nunes</strong>
              <small>Recruitment Operations</small>
            </span>
          </Button>
          <span className="topAutomationState running alwaysOnState">
            <i />
            24/7 Live
          </span>
        </div>

        <nav className="topNavLinks" aria-label="Recruitment console">
          <Button type="button" variant="ghost" size="sm" className={activeView === "overview" ? "active" : ""} onClick={() => navigateTo("overview")}>
            <HomeIcon /> <span>Overview</span>
          </Button>
          <Button type="button" variant="ghost" size="sm" className={activeView === "candidates" ? "active" : ""} onClick={() => navigateTo("candidates")}>
            <Users /> <span>Candidates</span>
          </Button>
          <Button type="button" variant="ghost" size="sm" className={activeView === "role-review" ? "active" : ""} onClick={() => openRoleReview()}>
            <BriefcaseBusiness /> <span>Roles</span>
          </Button>
          <Button type="button" variant="ghost" size="sm" className={activeView === "sent-responses" ? "active" : ""} onClick={() => navigateTo("sent-responses")}>
            <Send /> <span>Sent</span>
          </Button>
          <Button type="button" variant="ghost" size="sm" className={activeView === "message" ? "active" : ""} onClick={() => navigateTo("message")}>
            <MessageCircle /> <span>Messaging</span>
          </Button>
          <Button type="button" variant="ghost" size="sm" className={activeView === "activity" ? "active" : ""} onClick={() => navigateTo("activity")}>
            <Activity /> <span>Activity</span>
          </Button>
          <Button type="button" variant="ghost" size="sm" className={activeView === "settings" ? "active" : ""} onClick={() => navigateTo("settings")}>
            <SettingsIcon /> <span>Settings</span>
          </Button>
        </nav>

        <div className="topNavRight">
          <form className="topGlobalSearch" onSubmit={submitHeaderSearch}>
            <Search />
            <Input
              value={headerSearch}
              onChange={(event) => setHeaderSearch(event.target.value)}
              placeholder="Search candidates, roles, or keywords…"
              aria-label="Search candidates"
            />
          </form>

          <Button
            type="button"
            variant="ghost"
            size="icon"
            className={`topNoticeBell ${(data.backend_health?.errors || []).length > 0 || !data.email_verified ? "attention" : ""}`}
            aria-label="System attention status"
            onClick={() => navigateTo((data.backend_health?.errors || []).length > 0 ? "activity" : "settings")}
          >
            <Bell />
            {((data.backend_health?.errors || []).length > 0 || !data.email_verified) && <span />}
          </Button>

          <Button
            type="button"
            variant="outline"
            size="sm"
            className={`topWhatsappState ${(data.recruitment_pipeline?.whatsapp_status || "").toUpperCase() === "CONNECTED" ? "connected" : "waiting"}`}
            onClick={() => navigateTo("settings")}
            title="WhatsApp opens automatically with the approved Chrome session"
          >
            <MessageCircle />
            <span>WhatsApp {(data.recruitment_pipeline?.whatsapp_status || "CONNECTING").replaceAll("_", " ")}</span>
          </Button>

          <div className="topUserBadge" title={`Local workspace ${data.version}`}>N</div>
        </div>
      </header>

      <section className="content operationsContent">
        <header className="pageHero operationsHero">
          <div>
            <p className="eyebrow">Recruitment Operations</p>
            <h1>{activeView === "overview" ? "Recruitment Operations Hub" : VIEW_META[activeView].title}</h1>
            <p className="subhead">
              {activeView === "overview"
                ? "Automate, track and manage your hiring workflow from new applicants to completed hires."
                : VIEW_META[activeView].subtitle}
            </p>
          </div>
          {activeView === "overview" && (
            <div className="heroClock">
              <span>{clockNow.toLocaleDateString("en-IN", { weekday: "short", day: "2-digit", month: "short", year: "numeric" })}</span>
              <strong>{clockNow.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}</strong>
              <small className="running">
                <i /> 24/7 recruitment live
              </small>
            </div>
          )}
        </header>

        {viewError && activeView !== "overview" && (
          <div className="notice error">
            <span>{viewError}</span>
          </div>
        )}

        {activeView === "overview" && (
          <div className="operationsHub">
            {(data.backend_health?.errors || []).length > 0 && (
              <div className="opsCompactNotice warning">
                <span>
                  Backend recovered with {data.backend_health?.errors?.length || 0} subsystem warning(s). Live retry is continuing.
                </span>
                <Button type="button" variant="outline" size="sm" onClick={() => navigateTo("activity")}>View activity</Button>
              </div>
            )}

            {notice && (
              <div className={`opsCompactNotice ${notice.tone}`}>
                <span>{notice.text}</span>
                <Button type="button" variant="ghost" size="iconSm" aria-label="Dismiss notice" onClick={() => setNotice(null)}><X /></Button>
              </div>
            )}

            <section className="opsPanel hiringPipelinePanel">
              <div className="opsPanelHeader">
                <div className="opsPanelTitle">
                  <span className="opsPanelIcon blue"><BriefcaseBusiness /></span>
                  <div>
                    <h2>Hiring Pipeline</h2>
                    <p>Live view of candidates across your recruitment process</p>
                  </div>
                </div>
                <span className="liveDataPill"><Radio /> Live data</span>
              </div>

              <div className="pipelineStageGrid">
                <article className="pipelineStage stageBlue">
                  <div className="pipelineStageTop">
                    <Users />
                    <span className="pipelineBadge positive">+{data.live_detection?.detected_today || 0} today</span>
                  </div>
                  <strong>{data.stats.total || 0}</strong>
                  <h3>New Applicants</h3>
                  <p>Detected from Indeed</p>
                  <div className="stageProgress"><span style={{ width: "100%" }} /></div>
                </article>
                <ChevronRight className="pipelineArrow" />

                <article className="pipelineStage stageGreen">
                  <div className="pipelineStageTop">
                    <Mail />
                    <span className="pipelineBadge">{stagePercent(Number(data.stats.sent || 0))}%</span>
                  </div>
                  <strong>{data.stats.sent || 0}</strong>
                  <h3>Acknowledged</h3>
                  <p>Thank-you email sent</p>
                  <div className="stageProgress"><span style={{ width: `${stagePercent(Number(data.stats.sent || 0))}%` }} /></div>
                </article>
                <ChevronRight className="pipelineArrow" />

                <article className="pipelineStage stageAmber">
                  <div className="pipelineStageTop">
                    <Star />
                    <span className="pipelineBadge">{stagePercent(rankedCandidates)}%</span>
                  </div>
                  <strong>{rankedCandidates}</strong>
                  <h3>Ranked</h3>
                  <p>Resume evidence analyzed</p>
                  <div className="stageProgress"><span style={{ width: `${stagePercent(rankedCandidates)}%` }} /></div>
                </article>
                <ChevronRight className="pipelineArrow" />

                <article className="pipelineStage stagePurple">
                  <div className="pipelineStageTop">
                    <ShieldCheck />
                    <span className="pipelineBadge">{stagePercent(roleHrReviewTotal)}%</span>
                  </div>
                  <strong>{roleHrReviewTotal}</strong>
                  <h3>HR Review</h3>
                  <p>Ranked and waiting for HR</p>
                  <div className="stageProgress"><span style={{ width: `${stagePercent(roleHrReviewTotal)}%` }} /></div>
                </article>
                <ChevronRight className="pipelineArrow" />

                <article className="pipelineStage stageRed">
                  <div className="pipelineStageTop">
                    <CalendarDays />
                    <span className="pipelineBadge">{stagePercent(interviewCandidates)}%</span>
                  </div>
                  <strong>{interviewCandidates}</strong>
                  <h3>Interview</h3>
                  <p>Approved / outreach queued</p>
                  <div className="stageProgress"><span style={{ width: `${stagePercent(interviewCandidates)}%` }} /></div>
                </article>
                <ChevronRight className="pipelineArrow" />

                <article className="pipelineStage stageCompleted">
                  <div className="pipelineStageTop">
                    <CheckCircle2 />
                    <span className="pipelineBadge">{stagePercent(completedCandidates)}%</span>
                  </div>
                  <strong>{completedCandidates}</strong>
                  <h3>Completed</h3>
                  <p>Selected / hired / closed</p>
                  <div className="stageProgress"><span style={{ width: `${stagePercent(completedCandidates)}%` }} /></div>
                </article>
              </div>
            </section>

            <section className="opsPanel activeRolesPanel">
              <div className="opsPanelHeader">
                <div className="opsPanelTitle">
                  <span className="opsPanelIcon teal"><BriefcaseBusiness /></span>
                  <div>
                    <h2>Active Roles</h2>
                    <p>Track candidate progress for every role detected from Indeed</p>
                  </div>
                </div>
                <Button size="sm" onClick={() => openRoleReview()}>
                  View all roles
                  <ChevronRight />
                </Button>
              </div>

              <div className="activeRoleRows">
                {rolePipelineRows.length === 0 ? (
                  <div className="opsEmptyState">
                    <BriefcaseBusiness />
                    <div>
                      <strong>Waiting for Indeed roles</strong>
                      <span>Roles and their live ranking pipeline will appear automatically after the first successful scan.</span>
                    </div>
                  </div>
                ) : (
                  rolePipelineRows.map((role) => {
                    const lifecycle = (role.lifecycle_status || "UNKNOWN").toUpperCase();
                    const total = Number(role.applicant_count || 0);
                    return (
                      <article className="activeRoleRow" key={role.job_title}>
                        <div className="roleIdentityBlock">
                          <strong>{role.job_title}</strong>
                          <div className="roleMetaChips">
                            <span>Indeed</span>
                            <span className={lifecycle === "OPEN" ? "active" : lifecycle === "CLOSED" ? "closed" : "paused"}>
                              {lifecycle}
                            </span>
                            <span>{total} applicants</span>
                          </div>
                          <small>
                            {(role.report_status || "IDLE").replaceAll("_", " ")} role report
                          </small>
                        </div>

                        <div className="roleStageTrack">
                          <div className="roleMiniStage miniBlue">
                            <strong>{role.newCount}</strong><span>New</span>
                            <i><b style={{ width: `${roleStagePercent(role.newCount, total)}%` }} /></i>
                          </div>
                          <ChevronRight />
                          <div className="roleMiniStage miniGreen">
                            <strong>{role.acknowledged}</strong><span>Acknowledged</span>
                            <i><b style={{ width: `${roleStagePercent(role.acknowledged, total)}%` }} /></i>
                          </div>
                          <ChevronRight />
                          <div className="roleMiniStage miniAmber">
                            <strong>{role.ranked}</strong><span>Ranked</span>
                            <i><b style={{ width: `${roleStagePercent(role.ranked, total)}%` }} /></i>
                          </div>
                          <ChevronRight />
                          <div className="roleMiniStage miniPurple">
                            <strong>{role.hrReview}</strong><span>HR Review</span>
                            <i><b style={{ width: `${roleStagePercent(role.hrReview, total)}%` }} /></i>
                          </div>
                          <ChevronRight />
                          <div className="roleMiniStage miniRed">
                            <strong>{role.interview}</strong><span>Interview</span>
                            <i><b style={{ width: `${roleStagePercent(role.interview, total)}%` }} /></i>
                          </div>
                          <ChevronRight />
                          <div className="roleMiniStage miniDone">
                            <strong>{role.completed}</strong><span>Completed</span>
                            <i><b style={{ width: `${roleStagePercent(role.completed, total)}%` }} /></i>
                          </div>
                        </div>

                        <div className="roleTotalBlock">
                          <strong>{total}</strong>
                          <span>Total applicants</span>
                          <Button type="button" variant="outline" size="sm" onClick={() => openRoleReview(role.job_title)}>
                            View role <ChevronRight />
                          </Button>
                        </div>
                      </article>
                    );
                  })
                )}
              </div>
            </section>

            <div className="opsBottomGrid">
              <section className="opsPanel rankingStatusPanel">
                <div className="opsPanelHeader compact">
                  <div className="opsPanelTitle">
                    <span className="opsPanelIcon purple"><Sparkles /></span>
                    <div>
                      <h2>AI Ranking Status</h2>
                      <p>Continuous role-based resume evidence ranking</p>
                    </div>
                  </div>
                  <span className={`servicePill ${(data.role_ranking_status?.waiting_candidates || 0) > 0 ? "working" : "ready"}`}>
                    <i /> {(data.role_ranking_status?.waiting_candidates || 0) > 0 ? "Processing" : "Running"}
                  </span>
                </div>

                <div className="rankingStatusBody">
                  <div
                    className="rankingDonut"
                    style={{ background: `conic-gradient(#16b89a 0 ${rankingPercent}%, #e7eef8 ${rankingPercent}% 100%)` }}
                  >
                    <div><strong>{rankingPercent}%</strong><span>Complete</span></div>
                  </div>
                  <div className="rankingStatusList">
                    <div><i className="blue" /><strong>{data.stats.total || 0}</strong><span>Total candidates detected</span></div>
                    <div><i className="green" /><strong>{data.stats.sent || 0}</strong><span>Acknowledged via email</span></div>
                    <div><i className="amber" /><strong>{rankedCandidates}</strong><span>AI ranking complete</span></div>
                    <div><i className="purple" /><strong>{data.role_ranking_status?.waiting_candidates || 0}</strong><span>Processing / waiting</span></div>
                    <div><i className="gray" /><strong>{data.role_ranking_status?.roles_waiting_description || 0}</strong><span>Roles waiting description</span></div>
                  </div>
                </div>
              </section>

              <section className="opsPanel liveIndeedPanel">
                <div className="opsPanelHeader compact">
                  <div className="opsPanelTitle">
                    <span className="opsPanelIcon blue"><Radio /></span>
                    <div>
                      <h2>Live Indeed Detection</h2>
                      <p>Monitoring Indeed for new applicants</p>
                    </div>
                  </div>
                  <span className={`servicePill ${data.live_detection?.healthy ? "ready" : "working"}`}>
                    <i /> {data.live_detection?.healthy ? "Connected" : "Recovering"}
                  </span>
                </div>

                <div className="liveDetectionMain">
                  <span>Last detected</span>
                  <strong>{data.live_detection?.last_success_at ? relativeTime(data.live_detection.last_success_at) : "Waiting for first check"}</strong>
                  <small>{data.live_detection?.healthy ? "New applicants found automatically" : data.live_detection?.last_error || "Automatic reconnect is running"}</small>
                  <Button type="button" variant="outline" size="sm" loading={busy === "open-indeed"} loadingText="Opening…" onClick={() => runAction("open-indeed", "/api/open-indeed", "Indeed opened in Chrome.")}>View on Indeed <ExternalLink /></Button>
                </div>

                {!chromeConnected && (
                  <div className="chromeAttentionRow">
                    <div>
                      <strong>Chrome not connected</strong>
                      <span>Connect the existing Chrome session for real-time monitoring.</span>
                    </div>
                    <Button type="button" variant="warning" size="sm" loading={busy === "connect"} loadingText="Connecting…" onClick={() => runAction("connect", "/api/connect", "Chrome connection started.")}>Allow Chrome</Button>
                  </div>
                )}

                <div className="liveMiniMetrics">
                  <div><span>Seen today</span><strong>{data.live_detection?.seen_today || 0}</strong></div>
                  <div><span>Added today</span><strong>{data.live_detection?.detected_today || 0}</strong></div>
                  <div><span>Skipped</span><strong>{data.live_detection?.skipped_today || 0}</strong></div>
                  <div><span>Errors</span><strong className={(data.live_detection?.consecutive_failures || 0) > 0 ? "danger" : ""}>{data.live_detection?.consecutive_failures || 0}</strong></div>
                </div>
              </section>

              <section className="opsPanel candidateProcessingPanel">
                <div className="opsPanelHeader compact">
                  <div className="opsPanelTitle">
                    <span className="opsPanelIcon blue"><Settings2 /></span>
                    <div>
                      <h2>Candidate Processing</h2>
                      <p>Live status of applicant processing and communication</p>
                    </div>
                  </div>
                  <span className="servicePill ready"><i /> Processing</span>
                </div>

                <div className="processingList">
                  {recentProcessing.length === 0 ? (
                    <div className="opsEmptyState compact">
                      <Users />
                      <span>Waiting for the next live applicant.</span>
                    </div>
                  ) : (
                    recentProcessing.map((row) => {
                      const approved = (row.hr_flow?.hr_status || "").toUpperCase() === "APPROVED";
                      const ranked = (row.analysis_status || "").toUpperCase() === "READY";
                      const sent = (row.send_status || "").toUpperCase() === "SENT";
                      const statusText = approved
                        ? `HR approved${row.hr_flow?.interview_date ? ` · ${row.hr_flow.interview_date}` : ""}`
                        : ranked
                          ? `AI ranking complete${row.rank_position ? ` · Rank #${row.rank_position}` : ""}${row.match_score !== null && row.match_score !== undefined ? ` · ${Number(row.match_score).toFixed(0)}%` : ""}`
                          : sent
                            ? "Acknowledgement sent · ranking pending"
                            : reviewLabel(row.extraction_status);
                      return (
                        <div className="processingRow" key={row.id}>
                          <span className="processingAvatar">{(row.candidate_name || "C").slice(0, 2).toUpperCase()}</span>
                          <div>
                            <strong>{row.candidate_name || "Candidate"}</strong>
                            <span>{statusText}</span>
                          </div>
                          <small>{relativeTime(row.first_seen_at)}</small>
                        </div>
                      );
                    })
                  )}
                </div>
              </section>
            </div>
          </div>
        )}

        {activeView === "candidates" && (
        <section className="panel candidatePanel" id="candidates">
          <div className="panelHeader">
            <div>
              <p className="sectionLabel">Candidate Inbox</p>
              <h2>Applicants</h2>
              <p className="tableSubhead">
                Live Today shows applicants actually seen in today's Indeed scan. History keeps the older records separately.
              </p>
            </div>
            <div className="panelTools">
              <div className="candidateScopeToggle" role="group" aria-label="Candidate scope">
                <button
                  type="button"
                  className={candidateScope === "today" ? "active" : ""}
                  onClick={() => setCandidateScope("today")}
                >
                  Live Today
                  <span>{data.live_detection?.seen_today || 0}</span>
                </button>
                <button
                  type="button"
                  className={candidateScope === "history" ? "active" : ""}
                  onClick={() => setCandidateScope("history")}
                >
                  History
                  <span>{data.applicants.length}</span>
                </button>
              </div>
              <input
                className="searchInput"
                placeholder="Search candidates, jobs, email…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <span className="rowCount">{filteredApplicants.length} records</span>
            </div>
          </div>

          <div className="tableWrap candidateTableScroll">
            <table className="dataTable">
              <thead>
                <tr>
                  <th>Candidate</th>
                  <th>Position</th>
                  <th>Contact</th>
                  <th>Application</th>
                  <th>Mail</th>
                  <th>Reason</th>
                  <th>First detected</th>
                  <th>Last seen</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {filteredApplicants.length === 0 ? (
                  <tr>
                    <td colSpan={9}>
                      <div className="emptyState">
                        <strong>
                          {candidateScope === "today"
                            ? "Waiting for today's live Indeed scan"
                            : "No candidate records yet"}
                        </strong>
                        <span>
                          {candidateScope === "today"
                            ? "After a successful live scan, applicants seen today appear here automatically."
                            : "Candidate records will appear here after the Indeed Candidates page is active."}
                        </span>
                      </div>
                    </td>
                  </tr>
                ) : (
                  filteredApplicants.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <div className="candidateCell">
                          <div className="avatar">
                            {(row.candidate_name || "C").slice(0, 1).toUpperCase()}
                          </div>
                          <div>
                            <strong>{row.candidate_name || "Candidate"}</strong>
                            <span>{row.candidate_phone || "No phone available"}</span>
                            {row.indeed_status && (
                              <span className="indeedWorkflowStatus">
                                Indeed: {row.indeed_status}
                              </span>
                            )}
                          </div>
                        </div>
                      </td>
                      <td>
                        {row.job_title &&
                        !["the position", "position"].includes(
                          row.job_title.toLowerCase()
                        ) ? (
                          row.job_title
                        ) : (
                          <span className="pendingValue">Detecting role…</span>
                        )}
                      </td>
                      <td>
                        <div className="contactCell">
                          {row.candidate_email ? (
                            <span>{row.candidate_email}</span>
                          ) : (
                            <span className="pendingValue">
                              Detecting verified email…
                            </span>
                          )}
                          {row.resume_path && (
                            <a
                              href={`/backend/resume/${row.id}`}
                              target="_blank"
                              rel="noreferrer"
                            >
                              Resume
                            </a>
                          )}
                        </div>
                      </td>
                      <td>
                        <span
                          className={`badge ${
                            row.application_verified ? "success" : "warning"
                          }`}
                        >
                          {row.application_verified
                            ? "APPLICATION VERIFIED"
                            : "VERIFYING"}
                        </span>
                      </td>
                      <td>
                        <div className="communicationCell">
                          <span
                            className={`badge ${statusTone(row.send_status)}`}
                            title={row.send_error || undefined}
                          >
                            {(row.send_status || "Pending").replaceAll("_", " ")}
                          </span>
                        </div>
                      </td>
                      <td className="reasonCell">
                        <span title={row.decision_reason || row.send_error || undefined}>
                          {row.decision_reason
                            || row.send_error
                            || reviewLabel(row.extraction_status)}
                        </span>
                      </td>
                      <td>{formatDate(row.first_seen_at)}</td>
                      <td>
                        <span className={isLocalToday(row.last_seen_at) ? "liveSeenToday" : ""}>
                          {formatDate(row.last_seen_at)}
                        </span>
                      </td>
                      <td className="actionsCell">
                        {row.profile_url && (
                          <Button asChild variant="ghost" size="sm">
                            <a
                              href={row.profile_url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              <ExternalLink />
                              Open
                            </a>
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
        )}

        {activeView === "sent-responses" && (
          <section className="sentResponsesWorkspace" id="sent-responses">
            <div className="sentResponseSummaryGrid">
              <article className="sentSummaryCard primary">
                <div className="sentSummaryIcon">
                  <MailCheck />
                </div>
                <div>
                  <span>Total delivered</span>
                  <strong>{sentResponseSummary.total}</strong>
                  <small>Permanent acknowledgement records</small>
                </div>
              </article>

              <article className="sentSummaryCard">
                <div className="sentSummaryIcon">
                  <ShieldCheck />
                </div>
                <div>
                  <span>Unique recipients</span>
                  <strong>{sentResponseSummary.uniqueRecipients}</strong>
                  <small>Duplicate-protected email addresses</small>
                </div>
              </article>

              <article className="sentSummaryCard">
                <div className="sentSummaryIcon">
                  <CheckCircle2 />
                </div>
                <div>
                  <span>Role corrections</span>
                  <strong>{sentResponseSummary.corrections}</strong>
                  <small>One-time clarification messages sent</small>
                </div>
              </article>

              <article className="sentSummaryCard">
                <div className="sentSummaryIcon">
                  <Clock3 />
                </div>
                <div>
                  <span>Latest delivery</span>
                  <strong className="sentSummaryDate">
                    {sentResponseSummary.latest
                      ? formatDate(sentResponseSummary.latest)
                      : "No delivery yet"}
                  </strong>
                  <small>Most recent successful acknowledgement</small>
                </div>
              </article>
            </div>

            <section className="sentHistoryCard">
              <div className="sentHistoryHeader">
                <div className="sentHistoryTitleGroup">
                  <p className="sectionLabel">Communication History</p>
                  <div className="sentHistoryTitleRow">
                    <h2>Sent Responses</h2>
                    <span className="sentTotalPill">
                      {sentResponseSummary.total} delivered
                    </span>
                  </div>
                  <p className="tableSubhead">
                    Permanent delivery history. Acknowledged email addresses remain protected from duplicate sends.
                  </p>
                </div>

                <label className="sentSearchBox">
                  <Search />
                  <input
                    value={sentSearch}
                    onChange={(e) => setSentSearch(e.target.value)}
                    placeholder="Search candidate, email or role"
                    aria-label="Search sent responses"
                  />
                  <span>{filteredSentResponses.length}</span>
                </label>
              </div>

              <div className="sentHistoryScroll modernSentTableWrap">
                <table className="modernSentTable">
                  <colgroup>
                    <col className="sentColCandidate" />
                    <col className="sentColDelivery" />
                    <col className="sentColRole" />
                    <col className="sentColMessage" />
                    <col className="sentColTime" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>Candidate</th>
                      <th>Delivery</th>
                      <th>Applied role</th>
                      <th>Message</th>
                      <th>Sent</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredSentResponses.length === 0 ? (
                      <tr>
                        <td colSpan={5}>
                          <div className="emptyState sentEmptyState">
                            <div className="sentEmptyIcon">
                              <MailCheck />
                            </div>
                            <strong>
                              {sentSearch.trim()
                                ? "No matching sent responses"
                                : "No sent responses yet"}
                            </strong>
                            <span>
                              {sentSearch.trim()
                                ? "Try a different candidate, email address or role."
                                : "Successful acknowledgements will appear here automatically."}
                            </span>
                          </div>
                        </td>
                      </tr>
                    ) : (
                      filteredSentResponses.map((row) => (
                        <tr key={row.id}>
                          <td>
                            <div className="sentCandidateCell">
                              <div className="sentAvatar">
                                {(row.candidate_name || "C").slice(0, 1).toUpperCase()}
                              </div>
                              <div className="sentCandidateMeta">
                                <strong>{row.candidate_name || "Candidate"}</strong>
                                <span>{row.candidate_phone || "No phone recorded"}</span>
                              </div>
                            </div>
                          </td>

                          <td>
                            <div className="sentDeliveryCell">
                              <strong title={row.recipient_email}>
                                {row.recipient_email}
                              </strong>
                              <div className="sentDeliveryBadges">
                                <span className="deliveryBadge delivered">
                                  <CheckCircle2 />
                                  Delivered
                                </span>
                                {row.correction_status === "SENT" && (
                                  <span className="deliveryBadge corrected">
                                    Role corrected
                                  </span>
                                )}
                                {row.correction_required === 1 &&
                                  row.correction_status &&
                                  row.correction_status !== "SENT" && (
                                    <span className="deliveryBadge pending">
                                      {row.correction_status === "WAITING_FOR_ROLE"
                                        ? "Correction waiting"
                                        : "Correction pending"}
                                    </span>
                                  )}
                              </div>
                            </div>
                          </td>

                          <td>
                            <div className="sentRoleCell">
                              <span>{row.job_title || "Role not recorded"}</span>
                            </div>
                          </td>

                          <td>
                            <details className="modernResponseDetails">
                              <summary>
                                <div className="responseSummaryText">
                                  <strong>{row.subject || "Historical acknowledgement"}</strong>
                                  <span>View delivered message</span>
                                </div>
                                <span className="responseChevron">⌄</span>
                              </summary>
                              <div className="modernResponseSnapshot">
                                {row.body ? (
                                  <pre>{row.body}</pre>
                                ) : (
                                  <p>
                                    This acknowledgement was sent by an earlier version before exact message snapshots were stored. Recipient and delivery history remain preserved.
                                  </p>
                                )}

                                <div className="responseMetaRow">
                                  {row.smtp_transport && (
                                    <small>Transport: {row.smtp_transport}</small>
                                  )}
                                  {row.message_id && (
                                    <small>Message recorded</small>
                                  )}
                                </div>

                                {row.correction_sent_at && (
                                  <div className="modernCorrectionSnapshot">
                                    <div>
                                      <strong>One-time role correction</strong>
                                      <span>
                                        {row.correction_subject || "Correction sent"}
                                      </span>
                                    </div>
                                    {row.correction_body && (
                                      <pre>{row.correction_body}</pre>
                                    )}
                                    <small>
                                      Sent {formatDate(row.correction_sent_at)}
                                    </small>
                                  </div>
                                )}
                              </div>
                            </details>
                          </td>

                          <td>
                            <div className="sentTimeCell">
                              <strong>{formatDate(row.sent_at)}</strong>
                              <span>Successful</span>
                            </div>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </section>
        )}

        {activeView === "role-review" && (
          <RoleReview />
        )}

        {activeView === "settings" && (
          <article className="panel standalonePanel" id="settings">
            <div className="panelHeader">
              <div>
                <p className="sectionLabel">Configuration</p>
                <h2>Company & Monitoring</h2>
              </div>
              <div className="panelTools">
                <Button
                  size="sm"
                  disabled={busy === "settings"}
                  onClick={saveSettings}
                >
                  {busy === "settings" ? (
                    <LoaderCircle className="buttonSpinner" />
                  ) : (
                    <Save />
                  )}
                  {busy === "settings" ? "Saving" : "Save changes"}
                </Button>
              </div>
            </div>

            <div className="formGrid">
              <label>
                <span>Company name</span>
                <input
                  value={settingsDraft.company_name}
                  onChange={(e) => updateSetting("company_name", e.target.value)}
                />
              </label>
              <label>
                <span>Sender email</span>
                <input
                  type="email"
                  value={settingsDraft.company_email}
                  readOnly
                  aria-readonly="true"
                  title="Fixed sender account"
                />
              </label>
              <label>
                <span>Gmail App Password</span>
                <input
                  type="password"
                  value={password}
                  placeholder={
                    settingsDraft.smtp_app_password_set
                      ? "Configured • leave blank to keep"
                      : "Enter Gmail App Password"
                  }
                  onChange={(e) => {
                    setPassword(e.target.value);
                    settingsDirtyRef.current = true;
                  }}
                />
                <small className={`fieldHint ${data.email_verified ? "successHint" : ""}`}>
                  {data.email_verified
                    ? `Gmail verified${data.email_transport ? ` • ${data.email_transport}` : ""}`
                    : data.email_configured
                      ? (mailErrorSummary(data.email_last_error) || "Saved but not verified")
                      : "Use the 16-character Google App Password, not the normal Gmail password."}
                </small>
              </label>
            </div>

            <section className="pipelineSettingsCard savedConnectionsCard">
              <div className="pipelineSettingsHeader">
                <div>
                  <h3>Saved connections & preferences</h3>
                  <p>
                    Connect or configure once. The saved setup is reused after restart and future software updates.
                    A value changes only when you explicitly save or replace it.
                  </p>
                </div>
                <span className="servicePill ready"><i /> Persistent</span>
              </div>

              <div className="savedConnectionGrid">
                <div>
                  <span>Candidate Gmail</span>
                  <strong>{settingsDraft.persistence?.candidate_gmail_saved ? "Saved" : "Needs setup"}</strong>
                  <small>nuneslead@gmail.com</small>
                </div>
                <div>
                  <span>HR Gmail</span>
                  <strong>{settingsDraft.persistence?.hr_gmail_saved ? "Saved" : "Needs setup"}</strong>
                  <small>nunescbe@gmail.com</small>
                </div>
                <div>
                  <span>OpenAI ranking</span>
                  <strong>{settingsDraft.persistence?.openai_saved ? "Saved securely" : "Needs setup"}</strong>
                  <small>Windows-user secure storage</small>
                </div>
                <div>
                  <span>Indeed</span>
                  <strong>Reuse Chrome session</strong>
                  <small>Auto reconnect after restart</small>
                </div>
                <div>
                  <span>WhatsApp</span>
                  <strong>Reuse Chrome login</strong>
                  <small>QR again only if WhatsApp expires the session</small>
                </div>
                <div>
                  <span>GitHub</span>
                  <strong>Saved repository</strong>
                  <small>{settingsDraft.persistence?.github_repository || "Nunes-instruments/indeed_auomation"}</small>
                </div>
              </div>

              <div className="savedConnectionsNote">
                Saved does not mean an external provider can never expire a login. If Gmail, WhatsApp,
                Indeed, GitHub or OpenAI requires re-authentication, reconnect once and the new saved
                state becomes the one reused next time.
              </div>
            </section>

            <section className="pipelineSettingsCard rankingCostCard">
              <div className="pipelineSettingsHeader">
                <div>
                  <h3>Strict AI Ranking</h3>
                  <p>Keyword evidence is scored first. OpenAI is used only for mid-range matches, with a small score weight and a daily call cap.</p>
                </div>
                <span className={`servicePill ${settingsDraft.openai_ranking_key_configured ? "ready" : "working"}`}>
                  <i /> {settingsDraft.openai_ranking_key_configured ? "Ranking key secured" : "Ranking key required"}
                </span>
              </div>

              <div className="formGrid rankingSettingsGrid">
                <label>
                  <span>OpenAI ranking API key</span>
                  <input
                    type="password"
                    value={openaiRankingKey}
                    placeholder={settingsDraft.openai_ranking_key_configured ? "Configured securely • paste only to replace" : "Paste a new OpenAI API key"}
                    autoComplete="off"
                    onChange={(e) => setOpenaiRankingKey(e.target.value)}
                  />
                  <small className="fieldHint">Saved with Windows user DPAPI. It is never written to settings.json or GitHub and is used only by candidate ranking.</small>
                </label>
                <label>
                  <span>Ranking model</span>
                  <input value={settingsDraft.openai_ranking_model || "gpt-5.6-luna"} readOnly />
                  <small className="fieldHint">Low-cost semantic refinement after local keyword matching.</small>
                </label>
                <label>
                  <span>Daily OpenAI call cap</span>
                  <input
                    type="number" min="0" max="500"
                    value={settingsDraft.openai_ranking_daily_call_limit}
                    onChange={(e) => updateSetting("openai_ranking_daily_call_limit", Number(e.target.value || 0))}
                  />
                  <small className="fieldHint">Default 40. Once the cap is reached, ranking continues locally at no API cost.</small>
                </label>
                <label>
                  <span>AI ranking mode</span>
                  <select
                    value={settingsDraft.openai_ranking_enabled ? "on" : "off"}
                    onChange={(e) => updateSetting("openai_ranking_enabled", e.target.value === "on")}
                  >
                    <option value="on">ON – keyword first + AI refinement</option>
                    <option value="off">OFF – local keyword ranking only</option>
                  </select>
                </label>
              </div>
              <div className="pipelineSettingsActions">
                <Button type="button" size="sm" onClick={saveOpenAIRankingKey} disabled={busy === "openai-key" || !openaiRankingKey.trim()}>
                  <ShieldCheck />
                  {busy === "openai-key" ? "Securing…" : settingsDraft.openai_ranking_key_configured ? "Replace ranking key" : "Save ranking key"}
                </Button>
              </div>
            </section>

            <section className="pipelineSettingsCard">
              <div className="pipelineSettingsHeader">
                <div>
                  <p className="sectionLabel">Recruitment Communication</p>
                  <h3>Daily recruitment report + WhatsApp</h3>
                  <p>
                    One end-of-day email contains every ongoing Indeed role with a separate candidate list for each role. WhatsApp opens automatically with the same approved Chrome profile and reuses the saved login.
                  </p>
                </div>
                <div className="pipelineSettingsActions">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => runAction("hr-mail-test", "/api/recruitment/hr-mail/test", "HR ranking Gmail verified.")}
                    disabled={busy === "hr-mail-test" || !settingsDraft.hr_report_smtp_app_password_set}
                  >
                    <MailCheck />
                    {busy === "hr-mail-test" ? "Testing…" : "Test HR Gmail"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => runAction("daily-report-now", "/api/recruitment/daily-report/send-now", "Today's consolidated recruitment report was queued.")}
                    disabled={busy === "daily-report-now" || !settingsDraft.hr_report_smtp_app_password_set}
                  >
                    <Send />
                    {busy === "daily-report-now" ? "Queuing…" : "Send report now"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => runAction("whatsapp-connect", "/api/whatsapp/connect", "WhatsApp Web opened. Scan QR once if requested.")}
                    disabled={busy === "whatsapp-connect"}
                  >
                    <MessageCircle />
                    {busy === "whatsapp-connect" ? "Opening…" : "Open WhatsApp"}
                  </Button>
                </div>
              </div>

              <div className="formGrid">
                <label>
                  <span>HR ranking report sender</span>
                  <input type="email" value={settingsDraft.hr_report_sender_email || "nunescbe@gmail.com"} readOnly />
                  <small className="fieldHint">Fixed sender for the single consolidated end-of-day recruitment report.</small>
                </label>

                <label>
                  <span>Daily report recipient</span>
                  <input
                    type="email"
                    value={settingsDraft.hr_report_recipient || "nunescbe@gmail.com"}
                    onChange={(e) => updateSetting("hr_report_recipient", e.target.value)}
                  />
                </label>

                <label>
                  <span>HR Gmail App Password</span>
                  <input
                    type="password"
                    value={hrReportPassword}
                    placeholder={settingsDraft.hr_report_smtp_app_password_set ? "Configured • leave blank to keep" : "Enter App Password for nunescbe@gmail.com"}
                    onChange={(e) => {
                      setHrReportPassword(e.target.value);
                      settingsDirtyRef.current = true;
                    }}
                  />
                  <small className={`fieldHint ${data.recruitment_pipeline?.report_gmail_verified ? "successHint" : ""}`}>
                    {data.recruitment_pipeline?.report_gmail_verified
                      ? "HR ranking Gmail verified"
                      : data.recruitment_pipeline?.report_gmail_error || "Required for the automatic end-of-day consolidated recruitment report."}
                  </small>
                </label>

                <label>
                  <span>End-of-day report time</span>
                  <input
                    type="time"
                    value={settingsDraft.daily_report_time || "19:00"}
                    onChange={(e) => updateSetting("daily_report_time", e.target.value)}
                  />
                  <small className="fieldHint">Uses this Windows PC local time. Default: 7:00 PM.</small>
                </label>

                <label className="dailyReportToggleField">
                  <span>Automatic end-of-day report</span>
                  <select
                    value={settingsDraft.daily_consolidated_report_enabled ? "on" : "off"}
                    onChange={(e) => updateSetting("daily_consolidated_report_enabled", e.target.value === "on")}
                  >
                    <option value="on">ON – one consolidated email every day</option>
                    <option value="off">OFF</option>
                  </select>
                  <small className="fieldHint">Only ongoing roles are included. Closed/paused roles are excluded.</small>
                </label>

                <label>
                  <span>WhatsApp default country code</span>
                  <input
                    value={settingsDraft.whatsapp_default_country_code || "91"}
                    onChange={(e) => updateSetting("whatsapp_default_country_code", e.target.value.replace(/\D/g, ""))}
                  />
                  <small className="fieldHint">
                    Auto login: ON · WhatsApp: {data.recruitment_pipeline?.whatsapp_status || "CONNECTING"}
                  </small>
                </label>
              </div>

              <div className="pipelineStatusGrid">
                <div>
                  <span>Roles</span>
                  <strong>{data.recruitment_pipeline?.roles?.open || 0} open</strong>
                  <small>{data.recruitment_pipeline?.roles?.paused || 0} paused · {data.recruitment_pipeline?.roles?.closed || 0} closed</small>
                </div>
                <div>
                  <span>Notifications</span>
                  <strong>{data.recruitment_pipeline?.notifications?.queued || 0} queued</strong>
                  <small>{data.recruitment_pipeline?.notifications?.sent || 0} sent · {data.recruitment_pipeline?.notifications?.failed || 0} failed</small>
                </div>
                <div>
                  <span>HR approved</span>
                  <strong>{data.recruitment_pipeline?.hr_approved || 0}</strong>
                  <small>HR chooses the top count. Mon–Wed approvals interview next day; Thu–Fri approvals interview Monday.</small>
                </div>
                <div>
                  <span>Daily report</span>
                  <strong>{data.recruitment_pipeline?.daily_report?.time || settingsDraft.daily_report_time || "19:00"}</strong>
                  <small>Last sent: {formatDate(data.recruitment_pipeline?.daily_report?.last_sent_at)}</small>
                </div>
              </div>
            </section>
          </article>
        )}

        {activeView === "message" && (
          <article className="panel standalonePanel messageWorkspace" id="message">
            <div className="panelHeader">
              <div>
                <p className="sectionLabel">Communication Templates</p>
                <h2>Recruitment Messages</h2>
                <p className="tableSubhead">
                  Stage 1 is sent after a verified new application. Stage 2 is released only after HR approves an interview batch.
                </p>
              </div>
              <div className="panelTools">
                <Button size="sm" disabled={busy === "settings"} onClick={saveSettings}>
                  {busy === "settings" ? <LoaderCircle className="buttonSpinner" /> : <Save />}
                  {busy === "settings" ? "Saving" : "Save all messages"}
                </Button>
              </div>
            </div>

            <div className="messageStageGrid">
              <section className="messageStageCard">
                <div className="messageStageHeader">
                  <span className="messageStageNumber">1</span>
                  <div>
                    <strong>Application acknowledgement</strong>
                    <small>Automatic after candidate + role + contact verification</small>
                  </div>
                </div>

                <div className="messageEditor">
                  <label>
                    <span>Email subject</span>
                    <input value={settingsDraft.subject_template} onChange={(e) => updateSetting("subject_template", e.target.value)} />
                  </label>
                  <label>
                    <span>Email message</span>
                    <textarea rows={8} value={settingsDraft.body_template} onChange={(e) => updateSetting("body_template", e.target.value)} />
                  </label>
                  <label>
                    <span>WhatsApp message</span>
                    <textarea rows={6} value={settingsDraft.whatsapp_ack_template} onChange={(e) => updateSetting("whatsapp_ack_template", e.target.value)} />
                  </label>
                </div>
              </section>

              <section className="messageStageCard interviewMessageCard">
                <div className="messageStageHeader">
                  <span className="messageStageNumber">2</span>
                  <div>
                    <strong>HR interview invitation</strong>
                    <small>Fully editable · automatically sent only after HR approves the candidate/top-count batch</small>
                  </div>
                </div>

                <div className="messageEditor">
                  <label>
                    <span>Email subject</span>
                    <input value={settingsDraft.interview_subject_template} onChange={(e) => updateSetting("interview_subject_template", e.target.value)} />
                  </label>
                  <label>
                    <span>Email message</span>
                    <textarea rows={8} value={settingsDraft.interview_body_template} onChange={(e) => updateSetting("interview_body_template", e.target.value)} />
                  </label>
                  <label>
                    <span>WhatsApp message</span>
                    <textarea rows={6} value={settingsDraft.whatsapp_interview_template} onChange={(e) => updateSetting("whatsapp_interview_template", e.target.value)} />
                  </label>
                </div>
              </section>
            </div>

            <div className="messageRulesBar">
              <div>
                <strong>Available fields</strong>
                <span>{"{candidate_name}"} · {"{job_title}"} · {"{company_name}"} · {"{interview_date}"}</span>
              </div>
              <div>
                <strong>Interview scheduling</strong>
                <span>Approved Mon–Wed → next day · Approved Thu–Fri → Monday</span>
              </div>
            </div>
          </article>
        )}

        {activeView === "activity" && (
        <section className="panel standalonePanel" id="activity">
          <div className="panelHeader">
            <div>
              <p className="sectionLabel">System Activity</p>
              <h2>Recent Events</h2>
            </div>
            <span className="mutedText">
              Last check: {formatDate(data.stats.last_scan_at)}
            </span>
          </div>

          <div className="activityList">
            {data.logs.length === 0 ? (
              <div className="emptyState small">
                <strong>No activity recorded yet</strong>
                <span>Operational events will appear here.</span>
              </div>
            ) : (
              data.logs.slice(0, 20).map((row) => (
                <div className="activityRow" key={row.id}>
                  <span className={`logDot ${row.level.toLowerCase()}`} />
                  <div>
                    <strong>{row.message}</strong>
                    <span>{formatDate(row.created_at)}</span>
                  </div>
                  <small>{row.level}</small>
                </div>
              ))
            )}
          </div>
        </section>
        )}
      </section>
    </main>
  );
}
