// Pipeline de CI/CD: webhook de GitHub → Jenkins → servidor Linux con Docker Compose.
//
// Construcción normal (push a main): calidad → build → push → despliegue de la versión del commit.
// Rollback: "Build with Parameters" indicando IMAGE_TAG con una versión anterior. Se salta
// la construcción y se despliega esa imagen, que sigue en Docker Hub.

pipeline {
    agent any

    parameters {
        string(name: 'IMAGE_TAG', defaultValue: '', description: 'Rollback: versión a desplegar (SHA corto). Vacío = construir el commit actual.')
    }

    environment {
        DOCKER_HUB_REPO = 'cristianeduardoo/export-service'
        GITHUB_REPO = 'https://github.com/CristianEduardoo/backend-technical-challenge.git'
        BRANCH = 'main'
        // Usuario de despliegue sin privilegios (no root) y carpeta del proyecto en el servidor.
        SERVIDOR = 'deploy@mi-servidor'
        RUTA_SERVIDOR = '/home/deploy/export-service'
        // Mismas versiones que el Dockerfile y el compose.
        PYTHON_IMAGE = 'python:3.12.15-slim'
        POSTGRES_IMAGE = 'postgres:17.11'
    }

    stages {

        stage('Clonar Repositorio GitHub') {
            when { expression { !params.IMAGE_TAG } }
            steps {
                git branch: "${BRANCH}",
                    credentialsId: 'GitHubPat-CristianEduardoo',
                    url: "${GITHUB_REPO}"
            }
        }

        stage('Calcular Versión') {
            steps {
                script {
                    // Cada imagen se etiqueta con el commit: así cualquier versión se puede volver a desplegar.
                    env.VERSION = params.IMAGE_TAG ?: sh(script: 'git rev-parse --short HEAD', returnStdout: true).trim()
                    echo "Versión: ${env.VERSION}"
                }
            }
        }

        stage('Calidad: lint, tipos y tests') {
            when { expression { !params.IMAGE_TAG } }
            steps {
                // Los tests de integración necesitan PostgreSQL: uno temporal en una red propia del build.
                // CI=true hace que esos tests fallen (en lugar de saltarse) si la base de datos no está.
                // Se usa docker cp en lugar de montar la carpeta: Jenkins corre en un contenedor y sus
                // rutas no existen en el host de Docker.
                sh '''
                    docker network create ci-${BUILD_NUMBER}
                    docker run -d --name pg-${BUILD_NUMBER} --network ci-${BUILD_NUMBER} \
                        -e POSTGRES_PASSWORD=tests -e POSTGRES_DB=export_service_test ${POSTGRES_IMAGE}
                    until docker exec pg-${BUILD_NUMBER} pg_isready -U postgres; do sleep 1; done

                    docker create --name tests-${BUILD_NUMBER} --network ci-${BUILD_NUMBER} -w /app \
                        -e CI=true \
                        -e TEST_DATABASE_URL=postgresql+psycopg://postgres:tests@pg-${BUILD_NUMBER}:5432/export_service_test \
                        ${PYTHON_IMAGE} \
                        sh -c "pip install -q -e '.[dev]' && ruff check . && ruff format --check . && mypy app && pytest"
                    docker cp . tests-${BUILD_NUMBER}:/app
                    docker start -a tests-${BUILD_NUMBER}
                '''
            }
        }

        stage('Construir Imagen Docker') {
            when { expression { !params.IMAGE_TAG } }
            steps {
                // Dos etiquetas: la versión (para desplegar y hacer rollback) y latest (solo informativa).
                sh 'docker build --build-arg VERSION=${VERSION} -t ${DOCKER_HUB_REPO}:${VERSION} -t ${DOCKER_HUB_REPO}:latest .'
            }
        }

        stage('Subir Imagen a Docker Hub') {
            when { expression { !params.IMAGE_TAG } }
            steps {
                withCredentials([usernamePassword(credentialsId: 'DockerHub-CristianEduardoo', usernameVariable: 'DOCKER_USERNAME', passwordVariable: 'DOCKER_PASSWORD')]) {
                    sh 'echo $DOCKER_PASSWORD | docker login -u $DOCKER_USERNAME --password-stdin'
                }
                sh 'docker push ${DOCKER_HUB_REPO}:${VERSION}'
                sh 'docker push ${DOCKER_HUB_REPO}:latest'
                // Sin -a: se conservan las capas en caché para que el siguiente build sea rápido.
                sh 'docker builder prune -f'
                sh 'docker image prune -f'
            }
        }

        stage('Desplegar en Servidor') {
            steps {
                // 1. Actualiza el código (compose y ficheros que monta) a la rama desplegada.
                // 2. Escribe en el .env del servidor qué imagen y versión corren: queda registrado.
                // 3. pull + up -d: Compose solo recrea los contenedores cuya imagen cambió. Sin "down",
                //    no hay caída del servicio. Las migraciones las aplica el servicio migrate al arrancar.
                withCredentials([sshUserPrivateKey(credentialsId: 'ssh-export-service', keyFileVariable: 'SSH_KEY')]) {
                    sh '''
                        ssh -i $SSH_KEY ${SERVIDOR} "cd ${RUTA_SERVIDOR} \
                            && git pull \
                            && sed -i '/^IMAGE_REPO=/d; /^IMAGE_TAG=/d' .env \
                            && echo IMAGE_REPO=${DOCKER_HUB_REPO} >> .env \
                            && echo IMAGE_TAG=${VERSION} >> .env \
                            && docker compose pull migrate api worker \
                            && docker compose up -d \
                            && docker image prune -f"
                    '''
                }
            }
        }
    }

    post {
        always {
            // Limpia el PostgreSQL y los contenedores de tests de este build, y el workspace.
            sh 'docker rm -f tests-${BUILD_NUMBER} pg-${BUILD_NUMBER} || true'
            sh 'docker network rm ci-${BUILD_NUMBER} || true'
            cleanWs()
        }
    }
}
