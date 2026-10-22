import type { AskRequest, AskResponse, BrainClient, SearchRequest, SearchResponse } from "./schemas";

interface FakeArticle {
  id: string;
  title: string;
  category: string;
  settingPath: string | null;
  warning: string | null;
  text: string;
}

/**
 * A small, clearly simulated knowledge base so the inbox works offline (tests,
 * demo seed, mock evals). URLs point at example.com on purpose. The real facts
 * come from the Knowledge Brain service (Task 1) over HTTP or MCP.
 */
const ARTICLES: readonly FakeArticle[] = [
  {
    id: "sim-001",
    title: "Connect Stripe as your payment gateway",
    category: "Payments",
    settingPath: "Settings > Payment Settings > Stripe",
    warning: null,
    text: "Go to Settings > Payment Settings, choose Stripe, paste the publishable key and secret key from your Stripe dashboard, then save. Run a test signup on a low-priced plan before going live.",
  },
  {
    id: "sim-002",
    title: "Stripe webhooks and failed payments",
    category: "Payments",
    settingPath: "Settings > Payment Settings > Stripe > Webhook URL",
    warning: null,
    text: "Failed or declined card payments are only recorded when the Stripe webhook is configured. Copy the Webhook URL from Settings > Payment Settings > Stripe into Stripe Developers > Webhooks. Stripe retries a failed renewal automatically.",
  },
  {
    id: "sim-003",
    title: "Create and edit membership plans",
    category: "Membership",
    settingPath: "Membership > Membership Plans > Add New Plan",
    warning: null,
    text: "Open Membership > Membership Plans and click Add New Plan. Set the plan name, billing cycle, plan features and whether members on the plan appear in search results. Existing members keep their plan until you move them.",
  },
  {
    id: "sim-004",
    title: "Import members from a CSV file",
    category: "Members",
    settingPath: "Members > Import Members",
    warning: null,
    text: "Use Members > Import Members. The CSV must be UTF-8 encoded with a header row; email and first_name are required columns. Each file can hold up to 5,000 rows, so split larger lists into several files.",
  },
  {
    id: "sim-005",
    title: "Why a member listing is not public",
    category: "Members",
    settingPath: "Membership > Membership Plans > Edit Plan > Search Results Visibility",
    warning: null,
    text: "A listing only shows publicly when the member status is Active and their membership plan has Search Results Visibility switched on. Check the plan under Membership > Membership Plans > Edit Plan > Search Results Visibility.",
  },
  {
    id: "sim-006",
    title: "Search results are missing members",
    category: "Search",
    settingPath: "Settings > Search Settings > Rebuild Search Index",
    warning: null,
    text: "If members are missing from search results, rebuild the index from Settings > Search Settings > Rebuild Search Index. Location searches also depend on the radius setting and on members having a valid address.",
  },
  {
    id: "sim-007",
    title: "Change your site design template",
    category: "Design",
    settingPath: "Design Settings > Choose Theme",
    warning: "Custom CSS written for one theme may need adjusting after a theme switch.",
    text: "Switch templates from Design Settings > Choose Theme. Your content, members and pages stay in place. Add your own styles under Design Settings > Custom CSS.",
  },
  {
    id: "sim-008",
    title: "SEO settings for directory pages",
    category: "SEO",
    settingPath: "Settings > SEO Settings",
    warning: null,
    text: "Set meta title and description templates for directory pages under Settings > SEO Settings. The sitemap is generated automatically at /sitemap.xml and should be submitted in Google Search Console.",
  },
  {
    id: "sim-009",
    title: "Customize a widget",
    category: "Widgets",
    settingPath: "Toolbox > Widget Manager > Customize",
    warning: "Customizing this widget stops it receiving updates.",
    text: "Open Toolbox > Widget Manager, find the widget and click Customize to edit its code. A customized widget no longer receives product updates, so keep a note of your changes.",
  },
  {
    id: "sim-010",
    title: "Suspend past-due members automatically",
    category: "Membership",
    settingPath: "Settings > Advanced Settings > Members > Suspend Past Due",
    warning: null,
    text: "Turn on Settings > Advanced Settings > Members > Suspend Past Due to suspend members whose renewal payment has failed. They are reactivated as soon as the payment succeeds.",
  },
  {
    id: "sim-011",
    title: "Invoices and billing history",
    category: "Billing",
    settingPath: "My Account > Billing History",
    warning: null,
    text: "Members download their invoices from My Account > Billing History. Admins see every invoice under Finance > Invoices and can resend an invoice email from there.",
  },
  {
    id: "sim-012",
    title: "Edit email notification templates",
    category: "Email",
    settingPath: "Settings > Email Templates",
    warning: null,
    text: "Welcome, renewal and password emails are edited under Settings > Email Templates. Use the preview button to send yourself a test copy.",
  },
  {
    id: "sim-013",
    title: "Custom domain and SSL",
    category: "Domain",
    settingPath: "Settings > Domain Settings",
    warning: null,
    text: "Point your domain's DNS A record to the address shown in Settings > Domain Settings. SSL is issued automatically once DNS has propagated, which can take up to 48 hours.",
  },
  {
    id: "sim-014",
    title: "Add-ons marketplace",
    category: "Add-ons",
    settingPath: "Toolbox > Add-ons",
    warning: null,
    text: "Browse and install add-ons from Toolbox > Add-ons. Each add-on page lists what it changes and whether it needs extra setup.",
  },
];

const STOPWORDS = new Set(
  "a an and are as at be but by can do does for from have how i in is it me my of on or our please so that the their them there this to us we what when where which why with you your hi hello thanks".split(" "),
);

function terms(text: string): Set<string> {
  return new Set(
    (text.toLowerCase().match(/[a-z0-9]+/g) ?? []).filter((t) => t.length > 2 && !STOPWORDS.has(t)),
  );
}

function score(query: Set<string>, article: FakeArticle): number {
  const doc = terms(`${article.title} ${article.title} ${article.text}`);
  let hits = 0;
  for (const t of query) if (doc.has(t)) hits++;
  return query.size === 0 ? 0 : hits / Math.sqrt(query.size * doc.size);
}

const ANSWER_MIN_SCORE = 0.12;

export class FakeBrainClient implements BrainClient {
  readonly name = "brain-fake";
  private gapCounter = 0;

  constructor(private readonly available = true) {}

  private ranked(query: string, topK: number): Array<{ article: FakeArticle; score: number }> {
    if (!this.available) throw new Error("fake brain offline");
    const q = terms(query);
    return ARTICLES.map((article) => ({ article, score: score(q, article) }))
      .filter((r) => r.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, topK);
  }

  async ask(request: AskRequest): Promise<AskResponse> {
    const top = this.ranked(request.question, request.top_k ?? 8).filter((r) => r.score >= ANSWER_MIN_SCORE);
    const best = top[0];
    const usage = { input_tokens: 0, output_tokens: 0, cost_usd: 0, latency_ms: 1 };
    if (!best) {
      this.gapCounter += 1;
      return {
        status: "no_answer",
        answer: "",
        label: "unknown",
        setting_path: null,
        warnings: [],
        citations: [],
        confidence: 0,
        gap_ticket_id: `gap-sim-${this.gapCounter}`,
        model: "fake-brain",
        usage,
      };
    }
    return {
      status: "answered",
      answer: best.article.text,
      label: best.article.warning ? "widget_customization" : "setting",
      setting_path: best.article.settingPath,
      warnings: best.article.warning ? [best.article.warning] : [],
      citations: top.slice(0, 2).map(({ article }) => ({
        article_id: article.id,
        title: article.title,
        url: `https://support.example.com/articles/${article.id}`,
        quote: article.text,
        image_url: null,
      })),
      confidence: Math.min(0.95, 0.5 + best.score),
      gap_ticket_id: null,
      model: "fake-brain",
      usage,
    };
  }

  async search(request: SearchRequest): Promise<SearchResponse> {
    return {
      results: this.ranked(request.query, request.top_k ?? 8).map(({ article, score: s }) => ({
        chunk_id: `${article.id}-c1`,
        article_id: article.id,
        title: article.title,
        url: `https://support.example.com/articles/${article.id}`,
        category: article.category,
        folder: article.category,
        kind: "text" as const,
        text: article.text,
        score: s,
      })),
    };
  }
}
