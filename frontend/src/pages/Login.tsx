import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { authService } from "../lib/auth";
import { Shield, Lock, ArrowRight, Loader2 } from "lucide-react";

const Login: React.FC = () => {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const handleSSOLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email) {
      setError("Please provide an enterprise email");
      return;
    }
    
    setLoading(true);
    setError("");
    
    try {
      // In a real app, this sends you to the SSO provider (Okta/Azure)
      await authService.mockSSOLogin(email, name || email.split("@")[0]);
      navigate("/catalog-search");
    } catch (err: any) {
      setError("Enterprise SSO authentication failed. Please check your credentials.");
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
      <div className="max-w-md w-full">
        {/* Logo/Brand */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-primary/10 mb-4">
            <Shield className="h-8 w-8 text-primary" />
          </div>
          <h1 className="text-3xl font-black text-slate-900 tracking-tight">CodeMind</h1>
          <p className="text-slate-500 mt-2 font-medium">Enterprise Discovery Agent</p>
        </div>

        {/* Login Card */}
        <div className="bg-white rounded-3xl shadow-xl shadow-slate-200/50 border border-slate-100 p-8">
          <div className="mb-6">
            <h2 className="text-xl font-bold text-slate-900">Sign in</h2>
            <p className="text-slate-500 text-sm mt-1">Access the internal discovery portal</p>
          </div>

          <form onSubmit={handleSSOLogin} className="space-y-4">
            <div>
              <label className="block text-xs font-black uppercase tracking-widest text-slate-400 mb-1.5 ml-1">
                Enterprise Email
              </label>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="user@enterprise.com"
                className="w-full px-4 py-3 rounded-xl bg-slate-50 border-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary transition-all text-slate-900"
              />
            </div>

            <div>
              <label className="block text-xs font-black uppercase tracking-widest text-slate-400 mb-1.5 ml-1">
                Full Name (Optional)
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="John Doe"
                className="w-full px-4 py-3 rounded-xl bg-slate-50 border-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary transition-all text-slate-900"
              />
            </div>

            {error && (
              <div className="p-3 rounded-lg bg-rose-50 text-rose-600 text-xs font-bold border border-rose-100">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full flex items-center justify-center gap-2 bg-slate-900 text-white py-3.5 rounded-xl font-bold hover:bg-slate-800 transition-all shadow-lg active:scale-[0.98] disabled:opacity-70"
            >
              {loading ? (
                <Loader2 className="h-5 w-5 animate-spin" />
              ) : (
                <>
                  <Lock className="h-4 w-4" />
                  Continue with SSO
                  <ArrowRight className="h-4 w-4" />
                </>
              )}
            </button>
          </form>

          <div className="mt-8 pt-6 border-t border-slate-50 text-center">
            <p className="text-[10px] text-slate-400 uppercase font-bold tracking-widest">
              Secured by Enterprise OIDC
            </p>
          </div>
        </div>

        <p className="text-center text-slate-400 text-xs mt-8">
          Need access? Contact your administrator.
        </p>
      </div>
    </div>
  );
};

export default Login;
