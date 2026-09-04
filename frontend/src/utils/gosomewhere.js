
import { buildProjectRoute } from "@/utils/interpretationContext.mjs";

function goSegmentation() {
    this.isNavigator = false;
    if (this.$route.path === "/segmentation") {
        this.$message.success('您已经在该界面了哦')
    } else this.$router.push(buildProjectRoute("/segmentation", this.$route.query.project_id));
}

function goSpectralIndices() {
    this.isNavigator = false;
    if (this.$route.path === "/spectralindices") {
        this.$message.success('您已经在该界面了哦')
    } else this.$router.push(buildProjectRoute("/spectralindices", this.$route.query.project_id));
}

export { goSegmentation, goSpectralIndices }
