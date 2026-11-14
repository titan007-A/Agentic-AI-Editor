import Link from "next/link";

/** Terms of service, readable without logging in (linked from the Meta app settings). */
export default function TermsPage() {
  return (
    <main className="mx-auto max-w-2xl px-6 py-12 text-sm leading-relaxed">
      <h1 className="mb-2 text-2xl font-bold">Terms of Service</h1>
      <p className="mb-8 text-muted-foreground">Last updated: 14 November 2026</p>

      <section className="mb-6">
        <h2 className="mb-2 text-base font-semibold">1. The service</h2>
        <p>
          PJ Media Hub Editor is a video editing tool used by our team and the creators we work
          with. It edits videos, and, for Instagram accounts you connect, can publish Reels and read
          and reply to comments on your posts through Meta&apos;s official Instagram API.
        </p>
      </section>

      <section className="mb-6">
        <h2 className="mb-2 text-base font-semibold">2. Your accounts</h2>
        <p>
          You connect only Instagram accounts you own or manage, and you decide whether replies are
          drafted for you to send or sent on their own. You are responsible for what is published
          from your accounts, and for following Instagram&apos;s Terms of Use and Community
          Guidelines.
        </p>
      </section>

      <section className="mb-6">
        <h2 className="mb-2 text-base font-semibold">3. Fair use</h2>
        <p>
          Don&apos;t use the service to send spam, to mislead people, or to break Meta&apos;s
          Platform Terms. We may limit or turn off features to keep accounts safe.
        </p>
      </section>

      <section className="mb-6">
        <h2 className="mb-2 text-base font-semibold">4. Your data</h2>
        <p>
          How we handle your data is in our{" "}
          <Link href="/privacy/" className="underline">
            Privacy Policy
          </Link>
          ; how to delete it is on the{" "}
          <Link href="/data-deletion/" className="underline">
            data deletion page
          </Link>
          .
        </p>
      </section>

      <section className="mb-6">
        <h2 className="mb-2 text-base font-semibold">5. Changes</h2>
        <p>
          We may update these terms; the date above shows the latest version. Questions: see the
          contact in our{" "}
          <Link href="/privacy/" className="underline">
            Privacy Policy
          </Link>
          .
        </p>
      </section>
    </main>
  );
}
