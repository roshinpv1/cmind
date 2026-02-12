import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import { AdminLayout, UserLayout } from "./layouts/Layout";
import RepoList from "./pages/admin/RepoList";
import RepoIndex from "./pages/admin/RepoIndex";
import CatalogCreate from "./pages/admin/CatalogCreate";
import CatalogSearch from "./pages/user/CatalogSearch";

function App() {
  return (
    <Router>
      <Routes>
        {/* User Routes */}
        <Route path="/" element={<UserLayout><CatalogSearch /></UserLayout>} />
        <Route path="/search" element={<UserLayout><CatalogSearch /></UserLayout>} />

        {/* Admin Routes */}
        <Route path="/admin" element={<AdminLayout><RepoList /></AdminLayout>} />
        <Route path="/admin/repos" element={<AdminLayout><RepoList /></AdminLayout>} />
        <Route path="/admin/index" element={<AdminLayout><RepoIndex /></AdminLayout>} />
        <Route path="/admin/catalog/create" element={<AdminLayout><CatalogCreate /></AdminLayout>} />
        <Route path="/admin/catalog/view" element={<Navigate to="/search" replace />} /> {/* Re-use search for viewing */}

        {/* Catch all */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Router>
  );
}

export default App;
