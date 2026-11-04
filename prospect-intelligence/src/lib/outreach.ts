import type { Contact, Prospect } from "./types";

/** Deterministic templates built only from stored facts. Not AI-generated; nothing is sent automatically. */
export function outreach(p: Prospect, contacts: Contact[]) {
  const who = contacts.find((c) => c.verified) ?? contacts[0];
  const first = who?.name.split(" ")[0];
  const hi = first ? `Hi ${first},` : "Hello,";
  const place = [p.city, p.state].filter(Boolean).join(", ");
  const industry = p.vertical !== "other" ? p.vertical : "your industry";
  const ctx = `${p.name}${place ? ` in ${place}` : ""}`;
  const sign = "Best regards,\n[Your name]\nIT Architechs";
  return {
    recipient: who ? `${who.name}${who.title ? `, ${who.title}` : ""}${who.verified ? "" : " (unverified lead)"}` : "No contact on file — identify the right decision-maker first",
    intro: { subject: `Technology support for ${p.name}`, body: `${hi}\n\nI'm reaching out from IT Architechs. We help ${industry} organizations in Missouri and Southern Illinois with managed IT, cybersecurity, cloud, networking and disaster recovery.\n\nI'd welcome a short conversation to learn how technology supports operations at ${ctx} and whether a second perspective would be useful.\n\nWould a 15-minute call sometime next week work?\n\n${sign}` },
    executive: { subject: `Keeping ${p.name} running and protected`, body: `${hi}\n\nFor ${industry} businesses, uptime and data protection tend to sit close to the bottom line. IT Architechs partners with leadership teams to keep systems reliable, secure and recoverable, so internal staff can focus on the business.\n\nI'd like to understand your priorities at ${p.name} before suggesting anything. Are you open to a brief introductory call?\n\n${sign}` },
    phone: `Hello ${first ?? "[name]"}, this is [your name] with IT Architechs. We provide managed IT, cybersecurity and disaster recovery support to ${industry} companies in the region. I'm calling to learn how ${p.name} handles IT today and see whether a conversation would be worthwhile. Is now a good time for a couple of minutes?`,
    voicemail: `Hello ${first ?? "[name]"}, this is [your name] with IT Architechs, calling for ${p.name}. We support ${industry} businesses with IT management and cybersecurity. I'll follow up by email, and you can reach me at [phone]. Thank you.`,
    questions: ["How is IT supported today — in-house staff, an outside provider, or a mix?", "Which systems would stop operations if they were unavailable for a day?", "How confident are you in your ability to recover from a major outage or data loss?"],
    followUp: { subject: `Following up — ${p.name}`, body: `${hi}\n\nI wanted to follow up on my earlier note. If now isn't the right time, I understand. I'm glad to share a brief overview of how IT Architechs supports ${industry} organizations whenever it would be helpful.\n\n${sign}` },
    mailto: (subject: string, body: string) => `mailto:${who?.email ?? ""}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`,
  };
}

/** Service hypotheses tied to documented facts. Everything here is a sales hypothesis, not a finding. */
export function opportunities(p: Prospect, signals: number): { service: string; basis: string }[] {
  const o: { service: string; basis: string }[] = [];
  if ((p.facilities ?? 0) >= 2) o.push({ service: "Facility connectivity & VoIP", basis: `${p.facilities} facilities on record` });
  if (p.employeesEst != null) o.push({ service: "Managed / co-managed IT", basis: `${p.employeesEst} employees (${p.employeesSource})` });
  if (p.naics && /^(31|32|33)/.test(p.naics)) o.push({ service: "Manufacturing systems & ERP support", basis: `NAICS ${p.naics} (manufacturing)` });
  if (p.naics && /^(493|42)/.test(p.naics)) o.push({ service: "Warehouse/distribution systems support", basis: `NAICS ${p.naics}` });
  if (signals > 0) o.push({ service: "Cloud migration & business continuity", basis: `${signals} sourced growth signal(s)` });
  return o;
}
