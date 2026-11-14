import Link from "next/link";

/** How to delete one's data: the "User data deletion" instructions URL in the Meta app
 * settings, readable without logging in. */
export default function DataDeletionPage() {
  return (
    <main className="mx-auto max-w-2xl px-6 py-12 text-sm leading-relaxed">
      <h1 className="mb-2 text-2xl font-bold">Delete your data</h1>
      <p className="mb-8 text-muted-foreground">Last updated: 14 November 2026</p>

      <section className="mb-6">
        <h2 className="mb-2 text-base font-semibold">What we keep</h2>
        <p>
          For an Instagram account you connected: its ID, username, name, picture link, business
          portfolio ID, the access token from Meta, and the comments on your posts we read to
          answer, with our replies.
        </p>
      </section>

      <section className="mb-6">
        <h2 className="mb-2 text-base font-semibold">Delete it yourself, at once</h2>
        <ol className="list-decimal space-y-1 pl-5">
          <li>Log in and open the Instagram page.</li>
          <li>Press the bin button next to the account and confirm.</li>
        </ol>
        <p className="mt-2">
          The account, its token and all its comments and replies are deleted from our database
          right away.
        </p>
      </section>

      <section className="mb-6">
        <h2 className="mb-2 text-base font-semibold">
          Or remove our access on Instagram or Facebook
        </h2>
        <p>
          On Instagram: Settings, Website permissions, Apps and websites, then remove the app. In
          Meta Business Suite: Settings, Integrations, then remove the app from your business
          portfolio. Our token then stops working; ask us (below) to delete what we kept.
        </p>
      </section>

      <section className="mb-6">
        <h2 className="mb-2 text-base font-semibold">Ask us</h2>
        <p>
          Write to the contact in our{" "}
          <Link href="/privacy/" className="underline">
            Privacy Policy
          </Link>{" "}
          with your Instagram username and we delete everything we hold about it within 7 days, and
          tell you when it is done.
        </p>
      </section>
    </main>
  );
}
