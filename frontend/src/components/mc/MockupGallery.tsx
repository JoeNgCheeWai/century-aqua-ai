import { SectionTitle } from "@/components/mc/ui";

/** A labelled placehold.co address: what the image tool returns when it could not generate a picture. */
const isPlaceholder = (url: string) => url.startsWith("https://placehold.co/");

/**
 * The CMO's mockup images. Each address is either a Vercel Blob picture the image tool stored, or a labelled placeholder
 * (the backend drops every other address, see cmo_authorization.clean_mockup_urls). They are AI-generated drafts for a human to
 * judge, never a finished design, and the label says so. `large` is for the chat, where the picture is part of the answer;
 * the approval card keeps small thumbnails. A picture opens full size in a new tab, and only a real https address becomes a link.
 */
export default function MockupGallery({ urls, large = false }: { urls: string[]; large?: boolean }) {
  if (urls.length === 0) return null;
  const allPlaceholders = urls.every(isPlaceholder);
  return (
    <div>
      <SectionTitle>{allPlaceholders ? "Placeholder (no image could be generated)" : "AI-generated mockup (a draft to judge, not a finished design)"}</SectionTitle>
      <div className="flex flex-wrap gap-3">
        {urls.map((url) => {
          const placeholder = isPlaceholder(url);
          // A plain <img>: the picture lives on Vercel Blob (or is a placehold.co placeholder), not on this site, and the backend
          // only passes on addresses the image tool itself returned, so nothing here chooses which host to load.
          const image = (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={url}
              alt={placeholder ? "Placeholder: an image could not be generated" : "AI-generated mockup"}
              loading="lazy"
              referrerPolicy="no-referrer"
              className={`${large ? "max-h-80 max-w-full" : "h-28"} w-auto rounded-lg border border-slate-700`}
            />
          );
          return (
            <figure key={url} className="m-0 max-w-full">
              {url.startsWith("https://") ? (
                <a href={url} target="_blank" rel="noopener noreferrer" title="Open full size" className="block">
                  {image}
                </a>
              ) : (
                image
              )}
              {placeholder && <figcaption className="mt-1 text-xs text-amber-300">Placeholder: no image could be generated</figcaption>}
            </figure>
          );
        })}
      </div>
    </div>
  );
}
