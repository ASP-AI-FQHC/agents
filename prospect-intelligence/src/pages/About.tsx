export default function About() {
  return (
    <div className="max-w-2xl space-y-3 text-sm">
      <h1 className="text-xl font-semibold text-brand-900">About IT Architechs</h1>
      <p>IT Architechs provides managed IT, cybersecurity, cloud, networking, disaster recovery and technology consulting. <i>Discover. Qualify. Connect.</i></p>
      <p className="text-slate-500">Prospect Intelligence v0.1. Prospect data is stored only on this computer. Installed copies do not share records.</p>
      <h2 className="font-semibold">Data sources & privacy</h2>
      <ul className="list-disc pl-5 text-slate-600">
        <li>Live search uses OpenStreetMap data (© OpenStreetMap contributors, ODbL). Coverage is crowd-sourced and incomplete.</li>
        <li>CSV imports are labelled “Imported” and are your responsibility to hold the right to use.</li>
        <li>No employee counts, revenue, executives or contact details are inferred or invented. Missing data shows “Not available”.</li>
        <li>API keys are kept in Windows Credential Manager. Nothing is sent to an AI service.</li>
        <li>You can delete any record, or all data, from Settings.</li></ul>
    </div>
  );
}
