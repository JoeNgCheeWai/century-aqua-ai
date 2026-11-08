"use client";

import { useEffect, useRef, useState } from "react";

// --- Types ---
type Message = {
  role: "user" | "ai";
  text: string;
};

type Product = {
  name: string;
  tag: string;
  desc: string;
  hasDetails?: boolean;
  image?: string;
  imageAlt?: string;
  imagePosition?: string;
};

// --- Data ---
const NAV = [
  { label: "About", items: ["Our Story", "Leadership", "Sustainability", "Careers"] },
  { label: "Mission", items: ["Vision & Values", "Research Partners", "Impact Report"] },
  { label: "Products", items: ["Feeds & Nutrition", "Water Treatment", "Monitoring Sensors", "RAS Systems"] },
  { label: "Services", href: "#services", items: ["Farm Consulting", "Hatchery Setup", "Health Diagnostics", "AI Intelligence"] },
];

const PRODUCTS: Product[] = [
  { name: "Premium Feed Supplement", tag: "Nutrition", desc: "High-conversion nutritional supplements for fin fish and shrimp." },
  { name: "SND Probiotic", tag: "Water Treatment", desc: "Single-product solution eliminating ammonia, nitrite, and nitrate. Proven in RAS and high-density hatcheries.", hasDetails: true, image: "/images/products/snd-probiotic-product.jpg", imageAlt: "SND Probiotic product pouch in front of laboratory bioreactors" },
  { name: "Semi-Automated Inoculation System", tag: "Automation", desc: "Precision dosing and automated culture inoculation for hatcheries." },
  { name: "RAS Systems", tag: "Infrastructure", desc: "Recirculating systems engineered for maximum efficiency and biosecurity.", image: "/images/products/ras.jpg", imageAlt: "Recirculating aquaculture tank with bio-filtration unit and monitoring display", imagePosition: "object-center" },
  { name: "Fish Vaccines", tag: "Health", desc: "Advanced immunization solutions for proactive disease prevention." },
  { name: "Health Diagnostics", tag: "Biosecurity", desc: "Rapid disease screening and prevention programs." },
];

const SERVICES = [
  { name: "Farm Consulting", tag: "Advisory", desc: "Data-driven guidance on farm design, stocking strategy, and daily operations to improve survival, growth, and profitability." },
  { name: "Hatchery Setup", tag: "Infrastructure", desc: "End-to-end planning, equipment selection, and commissioning of hatcheries and nurseries built for biosecurity and consistent output." },
  { name: "Health Diagnostics", tag: "Biosecurity", desc: "Rapid pathogen screening and health monitoring programs that enable early detection and proactive disease management." },
  { name: "AI Intelligence", tag: "Technology", desc: "AI-powered analysis of farm and water-quality data that turns daily records into clear, actionable recommendations." },
];

// --- Components ---
function Navbar() {
  return (
    <header className="fixed top-0 z-40 w-full border-b border-white/10 bg-[#0A2342]/80 backdrop-blur-md">
      <nav className="mx-auto flex h-16 max-w-7xl items-center justify-between px-6">
        <a href="#" className="flex items-center gap-2 text-lg font-semibold tracking-wide text-white">
          <span className="inline-block h-3 w-3 rounded-full bg-[#00C9A7] shadow-[0_0_12px_#00C9A7]" />
          CENTURY <span className="font-light text-[#00C9A7]">AQUA</span>
        </a>
        <ul className="hidden items-center gap-1 md:flex">
          {NAV.map((menu) => (
            <li key={menu.label} className="group relative">
              <button className="flex items-center gap-1 rounded-md px-4 py-2 text-sm text-white/90 transition hover:text-[#00C9A7] focus:text-[#00C9A7] focus:outline-none">
                {menu.label}
                <svg className="h-3 w-3 transition group-hover:rotate-180" viewBox="0 0 20 20" fill="currentColor" aria-hidden>
                  <path d="M5.5 7.5 10 12l4.5-4.5" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" />
                </svg>
              </button>
              <div className="invisible absolute left-0 top-full w-56 translate-y-1 rounded-lg border border-white/10 bg-[#0A2342]/95 p-2 opacity-0 shadow-xl shadow-black/40 backdrop-blur-md transition group-focus-within:visible group-focus-within:translate-y-0 group-focus-within:opacity-100 group-hover:visible group-hover:translate-y-0 group-hover:opacity-100">
                {menu.items.map((item) => (
                  <a key={item} href={menu.href ?? "#"} className="block rounded-md px-3 py-2 text-sm text-white/80 transition hover:bg-[#00C9A7]/10 hover:text-[#00C9A7]">
                    {item}
                  </a>
                ))}
              </div>
            </li>
          ))}
        </ul>
        <a href="#chat" className="rounded-full border border-[#00C9A7] px-5 py-2 text-sm font-medium text-[#00C9A7] transition hover:bg-[#00C9A7] hover:text-[#0A2342]">
          Ask the AI
        </a>
      </nav>
    </header>
  );
}

function Hero() {
  const videoRef = useRef<HTMLVideoElement>(null);

  // Slow the background video down so small camera movements are less noticeable.
  // Also re-applied on loadedmetadata because some browsers reset the rate when the source loads.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const applyRate = () => {
      video.playbackRate = 0.75;
    };
    applyRate();
    video.addEventListener("loadedmetadata", applyRate);
    return () => video.removeEventListener("loadedmetadata", applyRate);
  }, []);

  return (
    <section className="relative flex h-screen w-full items-center justify-center overflow-hidden bg-[#0A2342]">
      <video ref={videoRef} className="absolute inset-0 h-full w-full scale-105 object-cover opacity-30 blur-[2px]" autoPlay muted loop playsInline>
        <source src="/videos/hero.mp4" type="video/mp4" />
      </video>
      <div className="absolute inset-0 bg-gradient-to-b from-[#0A2342]/80 via-[#0A2342]/50 to-[#0A2342]" />
      <div className="pointer-events-none absolute -bottom-40 left-1/2 h-96 w-[60rem] -translate-x-1/2 rounded-full bg-[#00C9A7]/20 blur-3xl" />
      <div className="relative z-10 mx-auto max-w-4xl px-6 text-center">
        <p className="mb-4 text-xs font-medium uppercase tracking-[0.3em] text-[#00C9A7]">Bio-Tech Sophistication</p>
        <h1 className="text-4xl font-semibold leading-tight text-white sm:text-6xl">
          Intelligence for the <span className="bg-gradient-to-r from-[#00C9A7] to-white bg-clip-text text-transparent">Future of Aquaculture</span>
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-lg text-white/70">
          Premium products, expert services and an AI engine that turns farm data into confident decisions.
        </p>
        <div className="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row">
          <a href="#products" className="rounded-full bg-[#00C9A7] px-8 py-3 font-medium text-[#0A2342] shadow-[0_0_30px_rgba(0,201,167,0.4)] transition hover:brightness-110">
            Explore Products
          </a>
          {/* Goes to the dashboard; signed-out visitors are sent on to Clerk's sign-in. */}
          <a href="/dashboard" className="rounded-full border border-white/30 px-8 py-3 font-medium text-white transition hover:border-[#00C9A7] hover:text-[#00C9A7]">
            Talk to CAIP
          </a>
        </div>
      </div>
    </section>
  );
}

function Reveal({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          observer.disconnect(); // animate once, then stop observing
        }
      },
      { threshold: 0.1 }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={`transition-[opacity,translate] duration-700 ease-out motion-reduce:transition-none ${
        visible ? "translate-y-0 opacity-100" : "translate-y-8 opacity-0 motion-reduce:translate-y-0 motion-reduce:opacity-100"
      } ${className}`}
    >
      {children}
    </div>
  );
}

function Products({ onProductClick }: { onProductClick: (name: string) => void }) {
  return (
    <section id="products" className="bg-[#0A2342] px-6 py-24">
      <Reveal className="mx-auto max-w-7xl">
        <p className="text-xs font-medium uppercase tracking-[0.3em] text-[#00C9A7]">Products</p>
        <h2 className="mt-3 text-3xl font-semibold text-white sm:text-4xl">Engineered for healthier harvests</h2>
        <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {PRODUCTS.map((p) => (
            <article 
              key={p.name} 
              onClick={() => p.hasDetails && onProductClick(p.name)}
              className={`group rounded-2xl border border-white/10 bg-white/5 p-6 transition hover:-translate-y-1 hover:border-[#00C9A7]/60 hover:shadow-[0_0_30px_rgba(0,201,167,0.15)] ${p.hasDetails ? 'cursor-pointer' : ''}`}
            >
                 <div className="mb-5 h-40 overflow-hidden rounded-xl ring-1 ring-white/10 bg-[#0A2342]">
                  {p.image ? (
                    <img src={p.image} alt={p.imageAlt ?? p.name} className={`h-full w-full object-cover transition duration-500 group-hover:scale-105 ${p.imagePosition ?? ""}`} />
                  ) : (
                    <div className="h-full w-full bg-gradient-to-br from-[#00C9A7]/30 to-[#0A2342]" />
                  )}
                 </div>
              <span className="text-xs font-medium uppercase tracking-widest text-[#00C9A7]">{p.tag}</span>
              <h3 className="mt-2 text-xl font-semibold text-white">{p.name}</h3>
              <p className="mt-2 text-sm leading-relaxed text-white/60">{p.desc}</p>
              {p.hasDetails && (
                <span className="mt-4 inline-block text-sm font-medium text-[#00C9A7] transition group-hover:translate-x-1">
                  Read Literature →
                </span>
              )}
            </article>
          ))}
        </div>
      </Reveal>
    </section>
  );
}

function Services() {
  return (
    <section id="services" className="bg-[#0A2342] px-6 py-24">
      <Reveal className="mx-auto max-w-7xl">
        <p className="text-xs font-medium uppercase tracking-[0.3em] text-[#00C9A7]">Services</p>
        <h2 className="mt-3 text-3xl font-semibold text-white sm:text-4xl">Expert Services</h2>
        <p className="mt-3 text-lg text-white/60">Comprehensive support for modern aquaculture</p>
        <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {SERVICES.map((s) => (
            <article
              key={s.name}
              className="group rounded-2xl border border-white/10 bg-white/5 p-6 transition hover:-translate-y-1 hover:border-[#00C9A7]/60 hover:shadow-[0_0_30px_rgba(0,201,167,0.15)]"
            >
              <div className="mb-5 h-40 overflow-hidden rounded-xl ring-1 ring-white/10 bg-[#0A2342]">
                <div className="h-full w-full bg-gradient-to-br from-[#00C9A7]/30 to-[#0A2342] transition duration-500 group-hover:scale-105" />
              </div>
              <span className="text-xs font-medium uppercase tracking-widest text-[#00C9A7]">{s.tag}</span>
              <h3 className="mt-2 text-xl font-semibold text-white">{s.name}</h3>
              <p className="mt-2 text-sm leading-relaxed text-white/60">{s.desc}</p>
            </article>
          ))}
        </div>
      </Reveal>
    </section>
  );
}

function ProductModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm" onClick={onClose}>
      <div className="relative max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-2xl border border-[#00C9A7]/30 bg-[#0A2342] p-8 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <button onClick={onClose} className="absolute right-4 top-4 text-2xl text-white/60 transition hover:text-white">✕</button>
           <div className="mb-6 flex h-80 w-full items-center justify-center overflow-hidden rounded-xl bg-white/5 ring-1 ring-[#00C9A7]/30 shadow-lg">
             <img src="/images/products/snd-probiotic-product.jpg" alt="SND Probiotic Packaging" className="h-full w-full object-contain p-4" />
           </div>
      
        <h2 className="text-3xl font-bold text-[#00C9A7]">SND Probiotic</h2>
        <p className="mt-2 text-lg text-white/80">Revolutionizing Aquaculture Water Quality</p>
        <p className="mt-1 text-sm italic text-white/50">Advanced Bioremediation Powered by Patented NUS Technology</p>
        
        <div className="mt-8 space-y-6 text-sm leading-relaxed text-white/80">
          <div>
            <h3 className="text-lg font-semibold text-white">The Challenge</h3>
            <p className="mt-2">In intensive aquaculture, managing nitrogenous waste is the ultimate bottleneck. Traditional biological filtration relies on a delicate, two-step bacterial process (<i>Nitrosomonas</i> and <i>Nitrobacter</i>). However, this method is highly unpredictable due to mismatched bacterial growth rates, often resulting in toxic ammonia spikes, severe nitrate accumulation, and the need for massive, resource-intensive water exchanges.</p>
          </div>

          <div>
            <h3 className="text-lg font-semibold text-white">The Solution: SND Probiotic</h3>
            <p className="mt-2">Developed in strategic collaboration with the <span className="font-semibold text-[#00C9A7]">National University of Singapore (NUS)</span>, SND Probiotic represents a paradigm shift in aquaculture water management. It is a highly specialized, patented consortium of beneficial bacteria engineered through a proprietary NUS production process. Unlike traditional methods, SND is a <span className="font-semibold text-white">single-product, comprehensive solution</span> capable of simultaneously and efficiently eliminating ammonia, nitrite, and nitrate.</p>
          </div>

          <div>
            <h3 className="text-lg font-semibold text-white">Proven, Field-Validated Results</h3>
            <p className="mt-2">SND Probiotic is backed by rigorous, multi-year empirical trials in real-world aquaculture environments:</p>
            <ul className="mt-3 space-y-2 list-disc pl-5">
              <li><span className="font-semibold text-white">Hatchery & Nursery Phase (2-Year Trial):</span> Maintained optimal ammonia/nitrite levels with <span className="font-semibold text-[#00C9A7]">zero nitrate accumulation</span> throughout the 6-week sea tilapia nursery period (0.5 cm to 4.5 cm), completely eliminating the need for daily water changes.</li>
              <li><span className="font-semibold text-white">Growout Phase (Current Static Pond Trials):</span> Successfully simulating open-pond static culture for Tilapia at high biomass densities (up to <span className="font-semibold text-[#00C9A7]">7 kg/m³</span>). Results show ammonia controlled to <span className="font-semibold text-[#00C9A7]">&lt; 1 ppm for 2 consecutive months</span> without a single water change, alongside significant sludge reduction and highly stable Dissolved Oxygen (DO) levels.</li>
            </ul>
          </div>

          <div>
            <h3 className="text-lg font-semibold text-white">Versatile Applications</h3>
            <ul className="mt-3 space-y-2 list-disc pl-5">
              <li><span className="font-semibold text-white">Recirculating Aquaculture Systems (RAS):</span> Maximizes biofilter efficiency and reduces water exchange rates.</li>
              <li><span className="font-semibold text-white">Hatcheries & Nurseries:</span> Ensures pristine water for sensitive larval and fingerling stages.</li>
              <li><span className="font-semibold text-white">Open Pond Static Culture:</span> Controls sludge and nitrogenous waste without mechanical water flushing.</li>
              <li><span className="font-semibold text-white">Wastewater Treatment:</span> Highly effective in treating aquaculture effluent before environmental discharge.</li>
            </ul>
          </div>
        </div>

        <div className="mt-8 flex justify-end">
          <button onClick={onClose} className="rounded-full bg-[#00C9A7] px-6 py-2 text-sm font-medium text-[#0A2342] transition hover:brightness-110">
            Close Literature
          </button>
        </div>
      </div>
    </div>
  );
}

function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [messages, setMessages] = useState<Message[]>([
    { role: "ai", text: "Welcome to Century Aquaculture. Ask me about species, water quality, feed, or farm operations." }
  ]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputValue.trim() || isLoading) return;

    const userMessage = inputValue.trim();
    setInputValue("");
    setMessages((prev) => [...prev, { role: "user", text: userMessage }]);
    setIsLoading(true);

    const conversationHistory = messages
      .filter(msg => msg.role === "user" || msg.role === "ai")
      .slice(-20)
      .map(msg => ({
        role: msg.role === "ai" ? "assistant" : "user",
        content: msg.text
      }));

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ 
          message: userMessage,
          history: conversationHistory
        }),
      });

      if (response.status === 429) {
        // Signed-in users who hit their plan limit (anonymous visitors are not metered yet).
        setMessages((prev) => [...prev, { role: "ai", text: "You've reached your message limit for this session. Open the dashboard to see when it resets." }]);
        return;
      }
      if (!response.ok) throw new Error(`Backend error: ${response.status}`);
      const data = await response.json();
      setMessages((prev) => [...prev, { role: "ai", text: data.reply }]);
    } catch (error) {
      console.error("Chat error:", error);
      setMessages((prev) => [...prev, { role: "ai", text: "Sorry, I'm having trouble connecting to the intelligence engine. Please ensure the backend is running and try again." }]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div id="chat" className="fixed bottom-6 right-6 z-50 flex flex-col items-end gap-3">
      {open && (
        <div className="flex h-[28rem] w-80 flex-col overflow-hidden rounded-2xl border border-[#00C9A7]/30 bg-[#0A2342] shadow-2xl shadow-black/50 sm:w-96">
          <div className="flex items-center justify-between border-b border-white/10 bg-[#0A2342] px-4 py-3">
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-[#00C9A7] shadow-[0_0_10px_#00C9A7]" />
              <span className="text-sm font-semibold text-white">CAIP Assistant</span>
            </div>
            <button onClick={() => setOpen(false)} className="text-white/60 transition hover:text-white">✕</button>
          </div>
          <div className="flex-1 space-y-3 overflow-y-auto p-4 text-sm">
            {messages.map((msg, index) => (
              <div key={index} className={`max-w-[85%] rounded-2xl px-4 py-2 ${msg.role === "user" ? "ml-auto rounded-tr-sm bg-[#00C9A7] text-[#0A2342]" : "rounded-tl-sm bg-white/10 text-white/90"}`}>
                {msg.text}
              </div>
            ))}
            {isLoading && <div className="max-w-[85%] rounded-2xl rounded-tl-sm bg-white/10 px-4 py-2 text-white/60"><span className="animate-pulse">Dr. Aqua is thinking...</span></div>}
          </div>
          <form onSubmit={handleSubmit} className="flex gap-2 border-t border-white/10 p-3">
            <input type="text" value={inputValue} onChange={(e) => setInputValue(e.target.value)} placeholder="Ask about aquaculture…" disabled={isLoading} className="flex-1 rounded-full border border-white/15 bg-white/5 px-4 py-2 text-sm text-white placeholder-white/40 focus:border-[#00C9A7] focus:outline-none disabled:opacity-50" />
            <button type="submit" disabled={isLoading || !inputValue.trim()} className="rounded-full bg-[#00C9A7] px-4 py-2 text-sm font-medium text-[#0A2342] transition hover:brightness-110 disabled:opacity-50">
              {isLoading ? "..." : "Send"}
            </button>
          </form>
        </div>
      )}
      <button onClick={() => setOpen((v) => !v)} className="flex h-14 w-14 items-center justify-center rounded-full bg-[#00C9A7] text-[#0A2342] shadow-[0_0_25px_rgba(0,201,167,0.6)] transition hover:scale-105">
        <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>
      </button>
    </div>
  );
}

export default function Home() {
  const [modalOpen, setModalOpen] = useState(false);

  return (
    <main className="bg-[#0A2342] text-white min-h-screen">
      <Navbar />
      <Hero />
      <Products onProductClick={() => setModalOpen(true)} />
      <Services />
      
      {modalOpen && <ProductModal onClose={() => setModalOpen(false)} />}

      <footer className="border-t border-white/10 px-6 py-8 text-center text-sm text-white/40">
        © 2026 Century Aquaculture. All rights reserved.
      </footer>
      <ChatWidget />
    </main>
  );
}