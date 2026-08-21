import { BrowserRouter, HashRouter, Navigate, Route, Routes } from 'react-router-dom'
import MainLayout from './layouts/MainLayout'
import HomePage from './pages/HomePage'
import LoginPage from './pages/LoginPage'
import ModelConfigPage from './pages/ModelConfigPage'
import KnowledgeBasePage from './pages/KnowledgeBasePage'
import KnowledgeDetailPage from './pages/KnowledgeDetailPage'
import CapturePage from './pages/CapturePage'
import ImagePage from './pages/ImagePage'
import MemoryPage from './pages/MemoryPage'
import GraphPage from './pages/GraphPage'
import MusicLibraryPage from './pages/MusicLibraryPage'
import ChatPage from './pages/ChatPage'
import GroupChatPage from './pages/GroupChatPage'
import ResearchPage from './pages/ResearchPage'
import AgentTaskPage from './pages/AgentTaskPage'
import NotifyChannelPage from './pages/NotifyChannelPage'
import AgentConfigPage from './pages/AgentConfigPage'
import SkillPage from './pages/SkillPage'
import ToolConfigPage from './pages/ToolConfigPage'
import SearchPage from './pages/SearchPage'
import FavoritesPage from './pages/FavoritesPage'
import ProfilePage from './pages/ProfilePage'
import SharePage from './pages/SharePage'
import ReportSharePage from './pages/ReportSharePage'
import JoinGroupPage from './pages/JoinGroupPage'
import TracesPage from './pages/TracesPage'
import ProjectsPage from './pages/ProjectsPage'
import AccessControlPage from './pages/AccessControlPage'
import ProjectDetailPage from './pages/ProjectDetailPage'
import RequireAuth from './components/RequireAuth'
import ErrorBoundary from './components/ErrorBoundary'

const Router = window.cometDesktop ? HashRouter : BrowserRouter

export default function App() {
  return (
    <ErrorBoundary>
      <Router>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/s/:token" element={<SharePage />} />
          <Route path="/r/:token" element={<ReportSharePage />} />
          <Route path="/groups/join/:code" element={<JoinGroupPage />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <MainLayout />
              </RequireAuth>
            }
          >
            <Route index element={<HomePage />} />
            <Route path="chat" element={<ChatPage />} />
            <Route path="group-chat" element={<GroupChatPage />} />
            <Route path="research" element={<ResearchPage />} />
            <Route path="projects" element={<ProjectsPage />} />
            <Route path="projects/:projectId" element={<ProjectDetailPage />} />
            <Route path="agent-tasks" element={<AgentTaskPage />} />
            <Route path="knowledge" element={<KnowledgeBasePage />} />
            <Route path="capture" element={<CapturePage />} />
            <Route path="knowledge-bases/:kbId" element={<KnowledgeDetailPage />} />
            <Route path="images" element={<ImagePage />} />
            <Route path="memory" element={<MemoryPage />} />
            <Route path="graph" element={<GraphPage />} />
            <Route path="music" element={<MusicLibraryPage />} />
            <Route path="search" element={<SearchPage />} />
            <Route path="favorites" element={<FavoritesPage />} />
            <Route path="traces" element={<TracesPage />} />
            <Route path="profile" element={<ProfilePage />} />
            <Route path="settings/models" element={<ModelConfigPage />} />
            <Route path="settings/agent" element={<AgentConfigPage />} />
            <Route path="settings/skills" element={<SkillPage />} />
            <Route path="settings/tools" element={<ToolConfigPage />} />
            <Route path="settings/notify" element={<NotifyChannelPage />} />
            <Route path="settings/access" element={<AccessControlPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Router>
    </ErrorBoundary>
  )
}
