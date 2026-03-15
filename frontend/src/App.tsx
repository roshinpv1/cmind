import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import { AdminLayout, UserLayout } from "./layouts/Layout";
import RepoList from "./pages/admin/RepoList";
import RepoIndex from "./pages/admin/RepoIndex";
import CatalogCreate from "./pages/admin/CatalogCreate";
import CatalogList from "./pages/admin/CatalogList";
import RepoEdit from "./pages/admin/RepoEdit";
import ProposalCreate from "./pages/admin/ProposalCreate";
import Dashboard from "./pages/admin/Dashboard";
import AgentCatalogSearch from "./pages/user/AgentCatalogSearch";
import ChatInterface from "./pages/user/ChatInterface";
import PlaybookStore from "./pages/user/PlaybookStore";
import PlaybookComposer from "./pages/admin/PlaybookComposer";
import Login from "./pages/Login";
import { authService } from "./lib/auth";

const ProtectedRoute = ({ children }: { children: React.ReactNode }) => {
  if (!authService.isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
};

function App() {
  return (
    <Router>
      <Routes>
        {/* Auth Route */}
        <Route path="/login" element={<Login />} />

        {/* Main Entry Points */}
        <Route path="/" element={<Navigate to="/catalog-search" replace />} />
        <Route path="/catalog-search" element={<ProtectedRoute><UserLayout><AgentCatalogSearch /></UserLayout></ProtectedRoute>} />
        <Route path="/reasoning-lab" element={<ProtectedRoute><UserLayout><ChatInterface /></UserLayout></ProtectedRoute>} />
        <Route path="/playbook-store" element={<ProtectedRoute><UserLayout><PlaybookStore /></UserLayout></ProtectedRoute>} />

        {/* Admin Routes */}
        <Route path="/admin" element={<ProtectedRoute><AdminLayout><Dashboard /></AdminLayout></ProtectedRoute>} />
        <Route path="/admin/dashboard" element={<ProtectedRoute><AdminLayout><Dashboard /></AdminLayout></ProtectedRoute>} />
        <Route path="/admin/repos" element={<ProtectedRoute><AdminLayout><RepoList /></AdminLayout></ProtectedRoute>} />
        <Route path="/admin/repos/:repoId/edit" element={<ProtectedRoute><AdminLayout><RepoEdit /></AdminLayout></ProtectedRoute>} />
        <Route path="/admin/index" element={<ProtectedRoute><AdminLayout><RepoIndex /></AdminLayout></ProtectedRoute>} />
        <Route path="/admin/catalogs" element={<ProtectedRoute><AdminLayout><CatalogList /></AdminLayout></ProtectedRoute>} />
        <Route path="/admin/catalog/create" element={<ProtectedRoute><AdminLayout><CatalogCreate /></AdminLayout></ProtectedRoute>} />
        <Route path="/admin/catalogs/propose" element={<ProtectedRoute><AdminLayout><ProposalCreate /></AdminLayout></ProtectedRoute>} />
        <Route path="/admin/playbook-composer" element={<ProtectedRoute><AdminLayout><PlaybookComposer /></AdminLayout></ProtectedRoute>} />
        <Route path="/admin/playbook-composer/:id" element={<ProtectedRoute><AdminLayout><PlaybookComposer /></AdminLayout></ProtectedRoute>} />

        {/* Catch all */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Router>
  );
}

export default App;

