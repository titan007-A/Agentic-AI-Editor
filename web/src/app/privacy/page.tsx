export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-2xl px-6 py-12 text-sm leading-relaxed">
      <h1 className="mb-2 text-2xl font-bold">Privacy Policy</h1>
      <p className="mb-8 text-muted-foreground">Last updated: 14 November 2026</p>

      <section className="mb-6">
        <h2 className="mb-2 text-base font-semibold">1. What we collect</h2>
        <p>
          When you connect an Instagram Business or Creator account (with Instagram or Facebook
          login), we store your Instagram account ID, username, display name, profile picture URL,
          your business portfolio ID, and an access token issued by Meta. To answer comments, we
          also keep the comments on your posts that arrive after you turn this on, with the reply we
          drafted or sent. We do not collect passwords or payment information.
        </p>
      </section>

      <section className="mb-6">
        <h2 className="mb-2 text-base font-semibold">2. How we use it</h2>
        <p>
          Your Instagram access token is used solely to publish Reels and to read and reply to
          comments on the accounts you explicitly connect, as you set it up. Replies are written on
          our own server; we do not share your token, comments or account data with any third party.
        </p>
      </section>

      <section className="mb-6">
        <h2 className="mb-2 text-base font-semibold">3. Data retention</h2>
        <p>
          Your account data, token and its comments are kept until you disconnect the account from
          the Instagram page, at which point they are permanently deleted from our database. See{" "}
          <a href="/data-deletion/" className="underline">
            how to delete your data
          </a>
          .
        </p>
      </section>

      <section className="mb-6">
        <h2 className="mb-2 text-base font-semibold">4. Security</h2>
        <p>
          Access tokens are stored in a private database on our server and are never exposed to
          other users or logged in plaintext.
        </p>
      </section>

      <section className="mb-6">
        <h2 className="mb-2 text-base font-semibold">5. Contact</h2>
        <p>
          For questions about this policy or to request deletion of your data, contact us at{" "}
          <a href="mailto:ujjwalsingh9170@gmail.com" className="underline">
            ujjwalsingh9170@gmail.com
          </a>
          .
        </p>
      </section>
    </main>
  );
}
