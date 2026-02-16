import { Link, useLocation } from "react-router-dom";
import { cn } from "../lib/utils";
import {
    Database,
    Search,
    Library,
    Settings,
    LayoutDashboard,
    Menu,
    X
} from "lucide-react";
import { useState } from "react";

export function AdminLayout({ children }: { children: React.ReactNode }) {
    const location = useLocation();
    const [isOpen, setIsOpen] = useState(false);

    const navigation = [
        { name: "Dashboard", href: "/admin", icon: LayoutDashboard },
        { name: "Index Repo", href: "/admin/index", icon: Database },
        { name: "Repositories", href: "/admin/repos", icon: Library },
        { name: "Create Catalog", href: "/admin/catalog/create", icon: Settings },
        { name: "View Catalog", href: "/admin/catalog/view", icon: Search },
    ];

    return (
        <div className="min-h-screen bg-gray-50 flex">
            {/* Mobile sidebar toggle */}
            <button
                className="lg:hidden fixed top-4 left-4 z-50 p-2 bg-white rounded-md shadow"
                onClick={() => setIsOpen(!isOpen)}
            >
                {isOpen ? <X /> : <Menu />}
            </button>

            {/* Sidebar */}
            <div className={cn(
                "fixed inset-y-0 left-0 z-40 w-64 bg-white border-r border-gray-200 transform transition-transform duration-200 ease-in-out lg:translate-x-0 lg:static lg:block",
                isOpen ? "translate-x-0" : "-translate-x-full"
            )}>
                <div className="h-16 flex items-center px-6 border-b border-gray-200">
                    <span className="text-xl font-bold text-primary">CodeMind</span>
                </div>
                <nav className="p-4 space-y-1">
                    {navigation.map((item) => {
                        const isActive = location.pathname === item.href;
                        return (
                            <Link
                                key={item.name}
                                to={item.href}
                                className={cn(
                                    "flex items-center px-4 py-3 text-sm font-medium rounded-lg transition-colors",
                                    isActive
                                        ? "bg-primary/10 text-primary"
                                        : "text-gray-600 hover:bg-gray-100 hover:text-gray-900"
                                )}
                            >
                                <item.icon className="w-5 h-5 mr-3" />
                                {item.name}
                            </Link>
                        );
                    })}
                    <div className="pt-4 mt-4 border-t border-gray-200">
                        <Link
                            to="/search"
                            className="flex items-center px-4 py-3 text-sm font-medium text-gray-600 rounded-lg hover:bg-gray-100 hover:text-gray-900"
                        >
                            <Search className="w-5 h-5 mr-3" />
                            User Search
                        </Link>
                    </div>
                </nav>
            </div>

            {/* Main content */}
            <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
                <main className="flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8">
                    {children}
                </main>
            </div>
        </div>
    );
}

export function UserLayout({ children }: { children: React.ReactNode }) {
    return (
        <div className="min-h-screen bg-gray-50">
            <header className="bg-white border-b border-gray-200 shadow-sm">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
                    <div className="flex items-center gap-8">
                        <span className="text-xl font-bold text-primary">CodeMind</span>
                        <nav className="hidden md:flex gap-4">
                            <Link to="/catalog-agent" className="text-sm font-medium text-gray-600 hover:text-primary">
                                Catalog Agent
                            </Link>
                            <Link to="/agent" className="text-sm font-medium text-gray-600 hover:text-primary">
                                Repo Chat
                            </Link>
                        </nav>
                    </div>
                    <Link to="/admin" className="text-sm text-gray-500 hover:text-gray-900">
                        Admin Portal
                    </Link>
                </div>
            </header>
            <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
                {children}
            </main>
        </div>
    );
}
