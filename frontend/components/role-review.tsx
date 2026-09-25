"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, Clock3, Mail, MessageCircle, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";


type Role = {
  job_title: string;
  job_description?: string | null;
  description_source?: string | null;
  description_status?: string | null;
  indeed_job_url?: string | null;
  lifecycle_status?: string | null;
  report_status?: string | null;
  report_sent_at?: string | null;
  report_error?: string | null;
  applicant_count: number;
  active_applicant_count?: number;
  removed_count?: number;
  analyzed_count: number;
  waiting_count: number;
  auto_shortlisted_count: number;
};

type Notification = {
  message_type: string;
  channel: string;
  status: string;
  error?: string | null;
  sent_at?: string | null;
};

type ReviewApplicant = {
  id: number;
  candidate_name?: string | null;
  candidate_email?: string | null;
  candidate_phone?: string | null;
  job_title?: string | null;
  indeed_status?: string | null;
  resume_path?: string | null;
  send_status?: string | null;
  extraction_status?: string | null;
  application_verified?: number | boolean | null;
  decision_reason?: string | null;
  first_seen_at?: string | null;
  analysis_status?: string | null;
  requirements_evidenced?: number | null;
  requirements_total?: number | null;
  match_score?: number | null;
  rank_position?: number | null;
  auto_shortlisted?: number | boolean | null;
  auto_bucket?: string | null;
  completed_at?: string | null;
  hr_flow?: {
    hr_status?: string | null;
    hr_approved_at?: string | null;
    interview_date?: string | null;
  };
  notifications?: Notification[];
  score_breakdown?: {
    top_matches?: string[];
    top_missing?: string[];
  };
  summary?: {
    education_evidence?: string[];
    experience_evidence?: string[];
  };
};

type RolePayload = {
  role: Role & {
    lifecycle?: {
      lifecycle_status?: string;
      report_status?: string;
      report_sent_at?: string | null;
      report_error?: string | null;
    };
  };
  applicants: ReviewApplicant[];
  active_applicants?: ReviewApplicant[];
  auto_shortlist: ReviewApplicant[];
  remaining: ReviewApplicant[];
  removed?: ReviewApplicant[];
  ranking: {
    ranked: number;
    waiting: number;
    auto_shortlisted: number;
    removed?: number;
    threshold: number;
  };
  notice: string;
  interview_schedule?: {
    interview_date?: string | null;
    rule?: string | null;
  };
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
    throw new Error(data?.message || data?.detail || `Request failed (${response.status})`);
  }

  return data;
};

const fmt = (value?: string | null) => {
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

const isToday = (value?: string | null) => {
  if (!value) return false;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return false;
  const now = new Date();
  return date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate();
};

const statusClass = (value?: string | null) => {
  const status = (value || "").toUpperCase();
  if (["SENT", "CONNECTED", "APPROVED", "OPEN", "READY"].includes(status)) return "success";
  if (["SEND_FAILED", "CLOSED", "ERROR"].includes(status)) return "danger";
  if (["PAUSED", "WAITING_LOGIN", "QUEUED", "PENDING", "UNKNOWN"].includes(status)) return "warning";
  return "neutral";
};

const scoreTone = (score?: number | null) => {
  const value = Number(score || 0);
  if (value >= 70) return "high";
  if (value >= 55) return "good";
  if (value >= 35) return "medium";
  return "low";
};

const notificationFor = (row: ReviewApplicant, type: string) =>
  (row.notifications || []).find((item) => item.message_type === type);

function ApplicantCard({
  row,
  roleOpen,
  onApprove,
  approving,
}: {
  row: ReviewApplicant;
  roleOpen: boolean;
  onApprove: (id: number) => Promise<void>;
  approving: boolean;
}) {
  const ready = row.analysis_status === "READY";
  const score = Number(row.match_score || 0);
  const approved = (row.hr_flow?.hr_status || "").toUpperCase() === "APPROVED";
  const ackWhatsApp = notificationFor(row, "ACK_WHATSAPP");
  const interviewEmail = notificationFor(row, "INTERVIEW_EMAIL");
  const interviewWhatsApp = notificationFor(row, "INTERVIEW_WHATSAPP");

  return (
    <article className="autoRankCard recruitmentCandidateCard">
      <div className="autoRankHead">
        <div className="autoRankIdentity">
          <div className="rankNumber">
            {ready && row.rank_position ? `#${row.rank_position}` : "…"}
          </div>
          <div>
            <strong>{row.candidate_name || "Candidate"}</strong>
            <span>{row.candidate_email || "Email not available"}</span>
            <small>{row.candidate_phone || "Phone not available"}</small>
          </div>
        </div>

        <div className="autoRankBadges">
          {ready && (
            <span className={`matchScore ${scoreTone(score)}`}>
              {score.toFixed(1)}%
            </span>
          )}
          {Boolean(row.auto_shortlisted) && (
            <span className="badge success">TOP MATCH</span>
          )}
          {isToday(row.first_seen_at) && (
            <span className="badge info">NEW TODAY</span>
          )}
          <span className={`badge ${row.send_status === "SENT" ? "success" : "neutral"}`}>
            EMAIL {row.send_status || "PENDING"}
          </span>
        </div>
      </div>

      <div className="candidatePipelineStrip">
        <div>
          <Mail />
          <span>Acknowledgement email</span>
          <strong>{row.send_status || "PENDING"}</strong>
        </div>
        <div>
          <MessageCircle />
          <span>Acknowledgement WhatsApp</span>
          <strong>{ackWhatsApp?.status || (row.candidate_phone ? "WAITING" : "NO PHONE")}</strong>
        </div>
        <div>
          <ShieldCheck />
          <span>HR decision</span>
          <strong>{row.hr_flow?.hr_status || "PENDING"}</strong>
        </div>
        <div className={approved ? "stageTwoApproved" : "stageTwoPending"}>
          <CheckCircle2 />
          <span>Stage 2 interview message</span>
          <strong>
            {approved
              ? `EMAIL ${interviewEmail?.status || (row.candidate_email ? "QUEUED" : "NO EMAIL")} · WA ${interviewWhatsApp?.status || (row.candidate_phone ? "QUEUED" : "NO PHONE")}`
              : "NOT SENT · HR APPROVAL REQUIRED"}
          </strong>
        </div>
      </div>

      <div className="autoRankMeta">
        <span>Applied {fmt(row.first_seen_at)}</span>
        <span>Evidence {row.requirements_evidenced || 0}/{row.requirements_total || 0}</span>
        <span>{row.analysis_status === "READY" ? "Ranking complete" : row.analysis_status || "Ranking queued"}</span>
      </div>

      {ready && (
        <>
          <div className="rankProgress" aria-label={`Match score ${score.toFixed(1)} percent`}>
            <span style={{ width: `${Math.max(0, Math.min(100, score))}%` }} />
          </div>

          <div className="rankReasonGrid">
            <div>
              <span>Strongest evidence</span>
              {(row.score_breakdown?.top_matches || []).slice(0, 3).length ? (
                <ul>
                  {(row.score_breakdown?.top_matches || []).slice(0, 3).map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : (
                <small>No strong requirement evidence found yet.</small>
              )}
            </div>
            <div>
              <span>Evidence not found</span>
              {(row.score_breakdown?.top_missing || []).slice(0, 3).length ? (
                <ul>
                  {(row.score_breakdown?.top_missing || []).slice(0, 3).map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : (
                <small>No major missing requirement evidence.</small>
              )}
            </div>
          </div>
        </>
      )}

      <div className="hrApprovalRow">
        <div>
          {approved ? (
            <>
              <strong className="approvedText">HR approved</strong>
              <span>
                Interview scheduled for {row.hr_flow?.interview_date || "the HR interview date"}
              </span>
              <small>
                Email {interviewEmail?.status || "QUEUED"} · WhatsApp {interviewWhatsApp?.status || "QUEUED"}
              </small>
            </>
          ) : (
            <>
              <strong>HR approval gate</strong>
              <span>Ranking is automatic. Interview outreach is sent only after HR approval.</span>
            </>
          )}
        </div>

        {!approved && (
          <Button
            size="sm"
            disabled={!ready || !roleOpen || approving}
            onClick={() => onApprove(row.id)}
          >
            <CheckCircle2 />
            {approving ? "Approving…" : "Approve this candidate"}
          </Button>
        )}
      </div>
    </article>
  );
}

export function RoleReview() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [selectedRole, setSelectedRole] = useState("");
  const [payload, setPayload] = useState<RolePayload | null>(null);
  const [message, setMessage] = useState("");
  const [search, setSearch] = useState("");
  const [approvingId, setApprovingId] = useState<number | null>(null);
  const [batchCount, setBatchCount] = useState(20);
  const [batchBusy, setBatchBusy] = useState(false);

  const loadRoles = useCallback(async () => {
    const result = await api("/api/role-review/roles");
    const rows = (result.roles || []) as Role[];
    setRoles(rows);

    let preferredRole = "";
    try {
      preferredRole = sessionStorage.getItem("nunes-selected-role") || "";
      if (preferredRole) sessionStorage.removeItem("nunes-selected-role");
    } catch {}

    setSelectedRole((current) => {
      if (preferredRole && rows.some((row) => row.job_title === preferredRole)) return preferredRole;
      if (current && rows.some((row) => row.job_title === current)) return current;
      return rows[0]?.job_title || "";
    });
  }, []);

  const loadRole = useCallback(async (jobTitle: string) => {
    if (!jobTitle) {
      setPayload(null);
      return;
    }
    const result = await api(`/api/role-review/role?job_title=${encodeURIComponent(jobTitle)}`);
    setPayload(result as RolePayload);
    setMessage("");
  }, []);

  useEffect(() => {
    loadRoles().catch((error) => setMessage(error instanceof Error ? error.message : String(error)));
    const timer = window.setInterval(() => loadRoles().catch(() => undefined), 5000);
    return () => window.clearInterval(timer);
  }, [loadRoles]);

  useEffect(() => {
    if (!selectedRole) return;
    loadRole(selectedRole).catch((error) => setMessage(error instanceof Error ? error.message : String(error)));
    const timer = window.setInterval(() => loadRole(selectedRole).catch(() => undefined), 2500);
    return () => window.clearInterval(timer);
  }, [selectedRole, loadRole]);

  const approve = useCallback(async (id: number) => {
    setApprovingId(id);
    setMessage("");
    try {
      const result = await api(`/api/recruitment/approve/${id}`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      setMessage(result.message || "HR approval saved.");
      if (selectedRole) await loadRole(selectedRole);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setApprovingId(null);
    }
  }, [selectedRole, loadRole]);

  const approveTop = useCallback(async () => {
    if (!selectedRole) return;
    const count = Math.max(1, Math.min(200, Number(batchCount || 0)));
    setBatchBusy(true);
    setMessage("");
    try {
      const result = await api("/api/recruitment/approve-top", {
        method: "POST",
        body: JSON.stringify({ job_title: selectedRole, count }),
      });
      setMessage(result.message || `HR approved the top ${count} candidates.`);
      await loadRole(selectedRole);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBatchBusy(false);
    }
  }, [selectedRole, batchCount, loadRole]);

  const filterRows = useCallback((rows: ReviewApplicant[]) => {
    const q = search.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((row) =>
      [
        row.candidate_name,
        row.candidate_email,
        row.candidate_phone,
        row.job_title,
        row.send_status,
        row.analysis_status,
        row.auto_bucket,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
  }, [search]);

  const shortlist = useMemo(() => filterRows(payload?.auto_shortlist || []), [payload?.auto_shortlist, filterRows]);
  const remaining = useMemo(() => filterRows(payload?.remaining || []), [payload?.remaining, filterRows]);
  const removed = useMemo(() => filterRows(payload?.removed || []), [payload?.removed, filterRows]);

  const role = payload?.role;
  const lifecycle = (role?.lifecycle?.lifecycle_status || role?.lifecycle_status || "UNKNOWN").toUpperCase();
  const roleOpen = lifecycle === "OPEN" || lifecycle === "UNKNOWN";
  const descriptionReady = Boolean(role?.job_description?.trim());

  return (
    <section className="roleReviewWorkspace autoRankingWorkspace">
      <div className="roleReviewTop autoRankingTop recruitmentPipelineTop">
        <div>
          <p className="sectionLabel">Automated Multi-Role Recruitment</p>
          <h2>Live Ranking + HR Approval Pipeline</h2>
          <p className="tableSubhead">
            Every active Indeed role has its own live ranking. A stronger new applicant automatically moves above earlier applicants. HR chooses the interview batch size before second-stage email + WhatsApp outreach is released.
          </p>
        </div>

        <div className="roleAutomationState">
          <span className={`statusDot ${roleOpen && descriptionReady ? "good" : "warn"}`} />
          <div>
            <strong>{selectedRole ? `${lifecycle} · ${selectedRole}` : "Waiting for roles"}</strong>
            <span>
              {lifecycle === "CLOSED" || lifecycle === "PAUSED"
                ? "Ranking/outreach is frozen for this role"
                : descriptionReady
                  ? "Continuous ranking active"
                  : "Waiting for official Indeed job description"}
            </span>
          </div>
        </div>
      </div>

      {message && <div className={`roleReviewNotice ${message.toLowerCase().includes("error") ? "error" : "success"}`}>{message}</div>}

      <div className="roleButtonsPanel">
        <div className="roleButtonsHeader">
          <div>
            <strong>Select role</strong>
            <span>{roles.length} roles detected from the connected Indeed Employer account</span>
          </div>
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search candidate in selected role…"
          />
        </div>
        <div className="roleButtonScroll">
          {roles.length === 0 ? (
            <span className="emptyRoleMessage">Waiting for verified Indeed roles…</span>
          ) : (
            roles.map((item) => (
              <Button
                key={item.job_title}
                type="button"
                variant={selectedRole === item.job_title ? "default" : "outline"}
                className={`roleChoiceButton ${selectedRole === item.job_title ? "active" : ""}`}
                onClick={() => setSelectedRole(item.job_title)}
              >
                <strong>{item.job_title}</strong>
                <span>
                  {item.active_applicant_count ?? item.applicant_count ?? 0} active · {(item.lifecycle_status || "UNKNOWN").toUpperCase()}
                </span>
              </Button>
            ))
          )}
        </div>
      </div>

      {role && (
        <div className="autoRankingMetrics recruitmentMetrics">
          <div><span>Total applicants</span><strong>{role.applicant_count || payload?.applicants?.length || 0}</strong></div>
          <div><span>Ranking complete</span><strong>{payload?.ranking?.ranked || 0}</strong></div>
          <div><span>Top matches</span><strong>{payload?.ranking?.auto_shortlisted || 0}</strong></div>
          <div><span>HR approved</span><strong>{(payload?.applicants || []).filter((item) => (item.hr_flow?.hr_status || "").toUpperCase() === "APPROVED").length}</strong></div>
          <div><span>Waiting</span><strong>{payload?.ranking?.waiting || 0}</strong></div>
          <div><span>Removed</span><strong>{payload?.ranking?.removed || 0}</strong></div>
          <div>
            <span>Role lifecycle</span>
            <strong className={`metricText ${statusClass(role.lifecycle?.report_status || role.report_status)}`}>
              {lifecycle}
            </strong>
          </div>
        </div>
      )}

      {role && (
        <section className="hrBatchApprovalPanel">
          <div className="hrBatchIntro">
            <div className="hrBatchIcon"><CheckCircle2 /></div>
            <div>
              <p className="sectionLabel">HR Interview Selection</p>
              <h3>Choose how many top-ranked candidates should attend the interview</h3>
              <p>
                AI keeps the ranking updated as new applicants arrive. HR chooses the final interview count; no interview invitation is sent until this button is clicked.
              </p>
            </div>
          </div>

          <div className="hrBatchControls">
            <div className="hrBatchPresets" aria-label="Interview candidate count">
              {[20, 30, 40].map((count) => (
                <Button
                  type="button"
                  key={count}
                  size="sm"
                  variant={batchCount === count ? "default" : "outline"}
                  className={batchCount === count ? "active" : ""}
                  onClick={() => setBatchCount(count)}
                >
                  Top {count}
                </Button>
              ))}
            </div>

            <label className="hrCustomCount">
              <span>Custom</span>
              <Input
                type="number"
                min={1}
                max={200}
                value={batchCount}
                onChange={(event) => setBatchCount(Math.max(1, Math.min(200, Number(event.target.value || 1))))}
              />
            </label>

            <div className="hrInterviewDate">
              <Clock3 />
              <span>
                Interview date
                <strong>{payload?.interview_schedule?.interview_date || "Calculated on approval"}</strong>
              </span>
            </div>

            <Button
              size="lg"
              loading={batchBusy}
              loadingText="Approving & queuing…"
              disabled={!roleOpen || Number(payload?.ranking?.ranked || 0) < 1}
              onClick={approveTop}
            >
              <CheckCircle2 />
              {`Approve Top ${batchCount} & Send Interview Message`}
            </Button>
          </div>

          <div className="hrBatchRule">
            <Clock3 />
            <span>{payload?.interview_schedule?.rule || "Mon–Wed approval → next day; Thu–Fri approval → Monday"}</span>
          </div>
          <div className="lateApplicantRule">
            <ShieldCheck />
            <span>Late applicants are re-ranked immediately. If a late applicant enters the selected Top N, they appear in the top list with Stage 2 marked NOT SENT. HR can click Approve Top N again; only newly eligible unsent candidates are added, so earlier interview messages are not duplicated.</span>
          </div>
        </section>
      )}

      <div className="rankingSafetyNote">
        Ranking uses only job-relevant role requirements and resume evidence. HR remains the decision maker and explicitly chooses the interview batch size before any second-stage outreach is released.
      </div>

      <div className="autoRankingColumns">
        <section className="roleReviewPanel autoShortlistPanel">
          <div className="roleReviewPanelHeader">
            <div>
              <strong>Top ranked candidates</strong>
              <span>{shortlist.length} strongest evidence matches</span>
            </div>
          </div>
          <div className="autoRankScroll">
            {shortlist.length === 0 ? (
              <div className="emptyState small">
                <strong>No top matches yet</strong>
                <span>{descriptionReady ? "New completed rankings will appear here automatically." : "Waiting for the official job description from Indeed."}</span>
              </div>
            ) : (
              shortlist.map((row) => (
                <ApplicantCard
                  row={row}
                  roleOpen={roleOpen}
                  onApprove={approve}
                  approving={approvingId === row.id}
                  key={`short-${row.id}`}
                />
              ))
            )}
          </div>
        </section>

        <section className="roleReviewPanel remainingCandidatesPanel">
          <div className="roleReviewPanelHeader">
            <div>
              <strong>Remaining candidates</strong>
              <span>{remaining.length} ranked or waiting applicants</span>
            </div>
          </div>
          <div className="autoRankScroll">
            {remaining.length === 0 ? (
              <div className="emptyState small">
                <strong>No remaining applicants</strong>
                <span>All active applicants are currently in the top-match group.</span>
              </div>
            ) : (
              remaining.map((row) => (
                <ApplicantCard
                  row={row}
                  roleOpen={roleOpen}
                  onApprove={approve}
                  approving={approvingId === row.id}
                  key={`remain-${row.id}`}
                />
              ))
            )}
          </div>
        </section>
      </div>

      {removed.length > 0 && (
        <details className="removedRankingHistory">
          <summary>Removed from active ranking ({removed.length})</summary>
          <div className="removedRankingList">
            {removed.map((row) => (
              <div className="removedRankingRow" key={`removed-${row.id}`}>
                <div>
                  <strong>{row.candidate_name || "Candidate"}</strong>
                  <span>{row.job_title || selectedRole}</span>
                </div>
                <span className="badge neutral">{row.indeed_status || "Terminal status"}</span>
              </div>
            ))}
          </div>
        </details>
      )}
    </section>
  );
}
